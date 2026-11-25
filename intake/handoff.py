"""接班库离线交接接纳与读回。

外来专用实收只保存在交接域表中，普通签卷仍只采用本地 receipts 表的资格。
接纳时按 README.md 的 V1 规则重新核定，并与原卷、原首执逐项核对；任何冲突
都整包拒收，已有实收和成果保持可读。
"""
import json
import sqlite3

from archive.acknowledgments import IssueAcknowledgment
from archive.bundles import RegionBundle
from archive.handoff_protocol import HandoffError, fingerprint, parse_envelope, validate_source_id
from archive.readback import IssuedBundle
from intake.evidence import EvidenceError, decode_evidence
from intake.store import IntakeStore
from intake.validate import validate_evidence
from review.decisions import decide_closure
from review.rollup import BundleLine


_HEX_FINGERPRINT = set('0123456789abcdef')


def _check_fingerprint(payload, fingerprint_value):
    if not isinstance(payload, bytes):
        raise HandoffError('handoff payload must be bytes')
    if (not isinstance(fingerprint_value, str) or len(fingerprint_value) != 64
            or any(char not in _HEX_FINGERPRINT for char in fingerprint_value)):
        raise HandoffError('fingerprint must be a lowercase SHA-256 hex string')
    if fingerprint(payload) != fingerprint_value:
        raise HandoffError('source fingerprint does not match the handed payload')


def _make_receipt(source_receipt_id, raw):
    # 延迟导入以避免与 intake.receipts 的模块加载顺序相互纠缠。
    from intake.receipts import Receipt
    try:
        evidence = validate_evidence(decode_evidence(raw))
    except EvidenceError as exc:
        raise HandoffError(f'carried receipt {source_receipt_id} fails V1 validation: {exc}') from exc
    return Receipt(source_receipt_id, evidence)


def _rebuild_and_verify(envelope):
    """按 V1 规则重新核定，并核对固定投影及原首执与原卷的对应。"""
    bundle_payload = envelope['bundle']
    raws = envelope['receipt_raws']
    receipts = tuple(
        _make_receipt(source_receipt_id, raws[source_receipt_id])
        for source_receipt_id in sorted(raws)
    )
    revision_id = bundle_payload['revision_id']
    target = bundle_payload['target']

    # 首执录制整次请求；外版选件只留在原首执里。
    try:
        request = json.loads(envelope['acknowledgment']['request_key'])
    except ValueError as exc:
        raise HandoffError('original request record is not valid JSON') from exc
    if (not isinstance(request, list) or len(request) != 3
            or request[0] != revision_id or request[1] != target):
        raise HandoffError('request record disagrees with the bundle revision or target')
    requested = request[2]
    if (not isinstance(requested, list) or not requested
            or any(type(value) is not int or not 0 < value < 2 ** 63 for value in requested)):
        raise HandoffError('request record carries invalid receipt identities')
    adopted = bundle_payload['receipt_ids']
    if set(adopted) - set(requested):
        raise HandoffError('bundle adopts receipts that the original request never selected')

    decision = decide_closure(revision_id, target, receipts)
    if decision.draft is None:
        raise HandoffError(f're-verification cannot establish the bundle: {decision.completeness.detail}')
    draft = decision.draft
    if tuple(draft.receipt_ids) != tuple(adopted):
        raise HandoffError('re-verified adopted receipts differ from the original bundle')
    if draft.total != bundle_payload['total']:
        raise HandoffError('re-verified scale total differs from the original bundle')
    if len(draft.lines) != len(bundle_payload['lines']):
        raise HandoffError('re-verified bundle lines differ from the original bundle')
    for actual_line, expected_line in zip(draft.lines, bundle_payload['lines']):
        if (actual_line.number, actual_line.area, actual_line.value, actual_line.origin,
                actual_line.occurrences) != (
            expected_line['number'], expected_line['area'], expected_line['value'],
            expected_line['origin'], tuple(expected_line['occurrences']),
        ):
            raise HandoffError(
                f're-verified projection disagrees on frozen row {expected_line["number"]}'
            )
    return receipts


def _reconcile_source(store, db, source_id, receipts):
    """同源录制、冻结行冲突沿用 V1 收件规则，并确定接班本地号。

    返回 (本包全部源实收号到本地号的映射, 需新写入交接域的实收条目)。
    """
    stored = store.load_source_receipts(db, source_id)
    recording_owner = {row[2]: (row[0], row[4]) for row in stored}
    facts_by_identity = {}
    for _source_receipt_id, _local_id, _recording_id, stored_revision, stored_raw in stored:
        stored_evidence = validate_evidence(decode_evidence(stored_raw))
        for occurrence in stored_evidence.rows:
            identity = (stored_revision, occurrence.number)
            facts = (occurrence.area, occurrence.value, occurrence.origin)
            previous = facts_by_identity.setdefault(identity, facts)
            if previous != facts:
                raise HandoffError(f'stored source evidence conflicts on frozen row {identity}')

    local_by_source = {}
    new_entries = {}
    package_recordings = set()
    candidate = store.handoff_watermark(db) + 1
    for receipt in receipts:
        evidence = receipt.evidence
        if evidence.recording_id in package_recordings:
            raise HandoffError(
                f'recording {evidence.recording_id} is carried twice with different source receipts'
            )
        package_recordings.add(evidence.recording_id)
        existing_recording = recording_owner.get(evidence.recording_id)
        if existing_recording is not None and existing_recording[1] != evidence.raw:
            raise HandoffError(
                f'recording {evidence.recording_id} was already handed over with different bytes'
            )
        for occurrence in evidence.rows:
            identity = (evidence.revision_id, occurrence.number)
            facts = (occurrence.area, occurrence.value, occurrence.origin)
            previous = facts_by_identity.setdefault(identity, facts)
            if previous != facts:
                raise HandoffError(
                    f'frozen revision {identity[0]} row {identity[1]} conflicts with an earlier source receipt'
                )

        existing = store.find_handoff_receipt(db, source_id, receipt.receipt_id)
        if existing is not None:
            local_receipt_id, existing_raw = existing
            if existing_raw != evidence.raw:
                raise HandoffError(
                    f'source receipt {receipt.receipt_id} was already bound with different bytes'
                )
        else:
            local_receipt_id = store.find_local_receipt_by_raw(db, evidence.raw)
            if local_receipt_id is None:
                # 分配与既有接班实收及本包先前分配均无冲突的本地号。
                while store.local_id_taken(db, candidate):
                    candidate += 1
                local_receipt_id = candidate
                candidate += 1
            new_entries[receipt.receipt_id] = (local_receipt_id, evidence)
        local_by_source[receipt.receipt_id] = local_receipt_id
    return local_by_source, new_entries


def accept_handoff(store: IntakeStore, source_id, payload, fingerprint_value):
    source_id = validate_source_id(source_id)
    _check_fingerprint(payload, fingerprint_value)
    envelope = parse_envelope(payload)
    if envelope['source_id'] != source_id:
        raise HandoffError('payload source id does not match the declared source')
    source_bundle_id = envelope['bundle']['bundle_id']
    receipts = _rebuild_and_verify(envelope)

    with store.transaction() as db:
        repeated = store.find_handoff(db, fingerprint_value)
        if repeated is not None:
            return repeated
        previous = store.find_source_bundle(db, source_id, source_bundle_id)
        if previous is not None:
            raise HandoffError(
                f'source bundle {source_bundle_id} was already accepted under a different handoff payload'
            )
        local_by_source, new_entries = _reconcile_source(store, db, source_id, receipts)
        try:
            handoff_id = store.save_handoff(db, source_id, source_bundle_id, fingerprint_value, payload)
            for source_receipt_id, (local_receipt_id, evidence) in sorted(new_entries.items()):
                store.save_handoff_receipt(
                    db, source_id, source_receipt_id, local_receipt_id,
                    evidence.recording_id, evidence.revision_id, evidence.raw,
                )
            for source_receipt_id, local_receipt_id in sorted(local_by_source.items()):
                store.save_handoff_binding(db, handoff_id, source_receipt_id, local_receipt_id)
        except sqlite3.Error as exc:
            # 事务随 with 块回滚，既有实收和成果保持可读。
            raise HandoffError(f'handoff storage unavailable: {exc}') from exc
    return handoff_id


def _rebuild_issued(envelope):
    bundle_payload = envelope['bundle']
    bundle_lines = tuple(
        BundleLine(
            line['number'], line['area'], line['value'], line['origin'],
            tuple(tuple(pair) for pair in line['occurrences']),
        )
        for line in bundle_payload['lines']
    )
    bundle = RegionBundle(
        bundle_payload['bundle_id'], bundle_payload['revision_id'], bundle_payload['target'],
        tuple(bundle_payload['receipt_ids']), bundle_lines, bundle_payload['total'],
    )
    acknowledgment_payload = envelope['acknowledgment']
    acknowledgment = IssueAcknowledgment(
        acknowledgment_payload['acknowledgment_id'],
        acknowledgment_payload['bundle_id'],
        acknowledgment_payload['request_key'],
    )
    return IssuedBundle(bundle, acknowledgment)


def read_handoff(store: IntakeStore, handoff_id):
    if type(handoff_id) is not int or not 0 < handoff_id < 2 ** 63:
        return None
    with store.read_snapshot() as db:
        loaded = store.load_handoff(db, handoff_id)
        if loaded is None:
            return None
        source_id, payload, bindings = loaded
        receipt_rows = []
        for source_receipt_id, _bound_local_id in bindings:
            row = store.find_handoff_receipt(db, source_id, source_receipt_id)
            if row is None:
                raise HandoffError(
                    f'handoff {handoff_id} is missing handed receipt {source_receipt_id}'
                )
            receipt_rows.append((source_receipt_id, *row))
        receipt_rows = tuple(receipt_rows)
    envelope = parse_envelope(payload)
    receipt_map = {
        source_receipt_id: (local_receipt_id, raw)
        for source_receipt_id, local_receipt_id, raw in receipt_rows
    }
    return {'source_id': source_id, 'issued': _rebuild_issued(envelope), 'receipts': receipt_map}
