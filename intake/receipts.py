from dataclasses import dataclass

from intake.evidence import Evidence, decode_evidence
from intake.known_rows import register_occurrences
from intake.store import IntakeStore
from intake.validate import EvidenceError, validate_evidence


class BadReceipt(LookupError):
    pass


@dataclass(frozen=True)
class Receipt:
    receipt_id: int
    evidence: Evidence


def receive(store: IntakeStore, raw):
    evidence = validate_evidence(decode_evidence(raw))
    with store.transaction() as db:
        previous = store.find_receipt(db, evidence.recording_id)
        if previous is not None:
            receipt_id, original = previous
            if original != raw:
                raise EvidenceError(f'recording {evidence.recording_id} was already received with different bytes')
        else:
            receipt_id = store.save_receipt(db, evidence.recording_id, evidence.revision_id, raw)
            register_occurrences(store, db, receipt_id, evidence)
    return Receipt(receipt_id, evidence)


def load_receipts(store: IntakeStore, ids):
    try:
        selected = tuple(ids)
    except TypeError as exc:
        raise BadReceipt('expected a collection of receipt numbers') from exc
    if isinstance(ids, (str, bytes)):
        raise BadReceipt('expected a collection of receipt numbers')
    if any(type(receipt_id) is not int or not 0 < receipt_id < 2**63 for receipt_id in selected):
        raise BadReceipt('invalid receipt number')
    selected = tuple(sorted(set(selected)))
    stored = store.fetch_receipts(selected)
    result = []
    for receipt_id in selected:
        if receipt_id not in stored:
            raise BadReceipt(f'unknown receipt {receipt_id}')
        evidence = validate_evidence(decode_evidence(stored[receipt_id]))
        result.append(Receipt(receipt_id, evidence))
    return tuple(result)


def accept_handoff(store: IntakeStore, source_id, payload, fingerprint):
    # 延迟导入：交接域实现依赖归档读回对象，而归档签发又依赖本模块。
    from intake.handoff import accept_handoff as _accept_handoff
    return _accept_handoff(store, source_id, payload, fingerprint)


def read_handoff(store: IntakeStore, handoff_id):
    from intake.handoff import read_handoff as _read_handoff
    return _read_handoff(store, handoff_id)
