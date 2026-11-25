import base64
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from archive.handoff_protocol import HandoffError, encode_envelope, parse_envelope
from archive.issue import close
from archive.readback import export_handoff
from handoff_sample import prepare as prepare_fixture
from intake.handoff import accept_handoff, read_handoff
from intake.receipts import BadReceipt, load_receipts
from intake.store import IntakeStore
from portal.export import export
from tests.fixtures import CenterCase


SOURCE_ID = 'day-shift-center-A'


def reencode(envelope, raw_overrides=None, bundle_id=None):
    """按给定原件覆盖和卷号重写交件，保持其余字段不动。"""
    payload_dict = {key: value for key, value in envelope.items() if key != 'receipt_raws'}
    if bundle_id is not None:
        payload_dict['bundle']['bundle_id'] = bundle_id
        payload_dict['acknowledgment']['bundle_id'] = bundle_id
    for item in payload_dict['receipts']:
        source_receipt_id = item['source_receipt_id']
        if raw_overrides and source_receipt_id in raw_overrides:
            item['raw_base64'] = base64.b64encode(raw_overrides[source_receipt_id]).decode('ascii')
    payload = encode_envelope(payload_dict)
    return payload, hashlib.sha256(payload).hexdigest()


class HandoffFixtureCase(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name) / 'fixture'
        root.mkdir()
        self.case = prepare_fixture(root)
        self.bundle_id = self.case['issued'].bundle.bundle_id
        self.origin_raw = self.case['origin_intake'].fetch_receipts([1, 2])

    def export(self, source_id=SOURCE_ID, bundle_id=None, archive=None, intake=None):
        return export_handoff(
            archive or self.case['origin_archive'],
            intake or self.case['origin_intake'],
            source_id,
            self.bundle_id if bundle_id is None else bundle_id,
        )

    def accept(self, payload, fingerprint_value, source_id=SOURCE_ID, store=None):
        return accept_handoff(store or self.case['receiver'], source_id, payload, fingerprint_value)

    def fresh_store(self, name):
        store = IntakeStore(Path(self.directory.name) / f'{name}.sqlite')
        store.initialize()
        return store


class TestHandoffExport(HandoffFixtureCase):
    def test_export_is_deterministic_and_self_describing(self):
        payload, fingerprint_value = self.export()
        self.assertEqual(hashlib.sha256(payload).hexdigest(), fingerprint_value)
        again, second_fingerprint = self.export()
        self.assertEqual(payload, again)
        self.assertEqual(fingerprint_value, second_fingerprint)

        envelope = parse_envelope(payload)
        self.assertEqual(envelope['source_id'], SOURCE_ID)
        self.assertEqual(envelope['bundle']['bundle_id'], self.bundle_id)
        self.assertEqual(envelope['bundle']['receipt_ids'], [1, 2])
        request = json.loads(envelope['acknowledgment']['request_key'])
        # 首执照录整次请求，外版选件只留在原首执里。
        self.assertEqual(request, [1, '甲', [1, 2, 3]])
        # 随件正文集合恰为实际采用的同版实收，外版（实收 3）不随卷。
        self.assertEqual(sorted(envelope['receipt_raws']), [1, 2])

    def test_unknown_bundle_and_missing_originals_raise(self):
        with self.assertRaises(ValueError):
            self.export(bundle_id=9999)
        with self.assertRaises(ValueError):
            self.export(intake=self.fresh_store('missing'))

    def test_source_id_must_be_stable_nonempty_text(self):
        for bad_source in ('', '   ', b'bytes', 7, None):
            with self.subTest(bad_source=bad_source):
                with self.assertRaises(ValueError):
                    self.export(source_id=bad_source)


class TestHandoffAcceptance(HandoffFixtureCase):
    def test_acceptance_reverifies_and_maps_receipts(self):
        handoff_id = self.accept(*self.export())
        self.assertIsInstance(handoff_id, int)
        self.assertGreater(handoff_id, 0)

        result = read_handoff(self.case['receiver'], handoff_id)
        self.assertEqual(result['source_id'], SOURCE_ID)
        # 卷和首执沿用源读回对象的含义。
        self.assertEqual(result['issued'], self.case['issued'])
        receipts = result['receipts']
        self.assertEqual(sorted(receipts), [1, 2])
        for source_receipt_id in (1, 2):
            local_receipt_id, raw = receipts[source_receipt_id]
            self.assertEqual(raw, self.origin_raw[source_receipt_id])
            self.assertIsInstance(raw, bytes)
        # 接班本地 8 已收源实收 2 的确切字节：首次绑定复用本地号。
        self.assertEqual(receipts[2][0], 8)
        # 源实收 1 字节不同，分配与既有接班实收无冲突的新本地号（高水位 8 之后）。
        self.assertEqual(receipts[1][0], 9)

    def test_repeated_handoff_returns_original_number(self):
        payload, fingerprint_value = self.export()
        first = self.accept(payload, fingerprint_value)
        second = self.accept(payload, fingerprint_value)
        self.assertEqual(first, second)
        self.assertEqual(
            self.case['receiver'].handoff_counts(),
            dict(handoffs=1, handoff_receipts=2, handoff_bundle_receipts=2),
        )

    def test_wrong_fingerprint_and_bad_payload_are_rejected(self):
        payload, fingerprint_value = self.export()
        fresh = self.fresh_store('fresh')
        with self.assertRaises(ValueError):
            accept_handoff(fresh, SOURCE_ID, payload, '0' * 64)
        with self.assertRaises(ValueError):
            accept_handoff(fresh, SOURCE_ID, payload, fingerprint_value.upper())
        with self.assertRaises(ValueError):
            accept_handoff(fresh, SOURCE_ID, b'not json',
                           hashlib.sha256(b'not json').hexdigest())
        with self.assertRaises(ValueError):
            accept_handoff(fresh, SOURCE_ID, b'', fingerprint_value)
        self.assertEqual(fresh.handoff_counts(),
                         dict(handoffs=0, handoff_receipts=0, handoff_bundle_receipts=0))
        self.assertEqual(fresh.counts(), dict(receipts=0, known_rows=0, occurrences=0))

    def test_same_source_same_bundle_with_different_payload_is_refused(self):
        payload, fingerprint_value = self.export()
        handoff_id = self.accept(payload, fingerprint_value)
        # 同源同卷号的异文（changed 库中实收 2 的 JSON 字节与先前来源件不同）。
        variant_payload, variant_fingerprint = self.export(
            archive=self.case['changed_archive'], intake=self.case['changed_intake']
        )
        self.assertNotEqual(variant_payload, payload)
        with self.assertRaises(HandoffError):
            self.accept(variant_payload, variant_fingerprint)
        # 整包拒收后既有实收和成果保持可读。
        self.assertEqual(read_handoff(self.case['receiver'], handoff_id)['issued'],
                         self.case['issued'])
        self.assertEqual(
            self.case['receiver'].handoff_counts(),
            dict(handoffs=1, handoff_receipts=2, handoff_bundle_receipts=2),
        )

    def test_cross_bundle_variant_text_for_same_source_is_refused(self):
        first_payload, first_fp = self.export()
        handoff_id = self.accept(first_payload, first_fp)
        # 乙区卷复用同一录制号，但其中实收 2 的字节与先前来源件不同（同源异文）。
        # 两卷以同一来源标识移交到已持原卷的接班库，冲突在同源核定处整包拒收。
        variant_payload, variant_fp = export_handoff(
            self.case['changed_archive'], self.case['changed_intake'], SOURCE_ID, 2
        )
        with self.assertRaises(HandoffError):
            self.accept(variant_payload, variant_fp)
        # 整包拒收后既有实收和成果保持可读。
        self.assertEqual(read_handoff(self.case['receiver'], handoff_id)['issued'],
                         self.case['issued'])
        self.assertEqual(
            self.case['receiver'].handoff_counts(),
            dict(handoffs=1, handoff_receipts=2, handoff_bundle_receipts=2),
        )

        # 全新接班库从未收过该来源的异文，乙区卷作为其首件正常接纳并独立核定。
        pristine = self.fresh_store('cross')
        variant_id = accept_handoff(pristine, SOURCE_ID, variant_payload, variant_fp)
        variant_read = read_handoff(pristine, variant_id)
        self.assertEqual(variant_read['source_id'], SOURCE_ID)
        self.assertEqual(variant_read['issued'].bundle.bundle_id, 2)
        self.assertEqual(variant_read['issued'].bundle.target, '乙')
        self.assertEqual(variant_read['issued'].bundle.total, 5)

    def test_source_id_namespace_is_distinct(self):
        payload, fingerprint_value = self.export()
        first = self.accept(payload, fingerprint_value)
        other_payload, other_fp = self.export(source_id='other-center')
        second = self.accept(other_payload, other_fp, source_id='other-center')
        self.assertNotEqual(first, second)
        other_result = read_handoff(self.case['receiver'], second)
        self.assertEqual(other_result['source_id'], 'other-center')
        # 不同来源的原号各自解释；相同字节仍复用接班本地号，新字节取新高水位。
        self.assertEqual(other_result['receipts'][2][0], 8)
        self.assertEqual(other_result['receipts'][1][0], 10)
        # 来源标识不匹配时不得接纳。
        with self.assertRaises(HandoffError):
            self.accept(payload, fingerprint_value, source_id='spoofed-center')

    def test_reverification_catches_tampered_projection_and_origins(self):
        payload, fingerprint_value = self.export()
        envelope = parse_envelope(payload)
        original_raw = envelope['receipt_raws'][1]
        data = json.loads(original_raw)

        with self.subTest(tamper='field_origin'):
            changed = json.loads(json.dumps(data, ensure_ascii=False))
            changed['rows'][0]['origin'] = '伪造/A'
            tampered, tampered_fp = reencode(
                envelope, {1: json.dumps(changed, ensure_ascii=False,
                                         separators=(',', ':')).encode('utf-8')}
            )
            with self.assertRaises(HandoffError):
                self.accept(tampered, tampered_fp, store=self.fresh_store('origin'))

        with self.subTest(tamper='download_origin_position'):
            changed = json.loads(json.dumps(data, ensure_ascii=False))
            changed['rows'][0]['position'] = 2
            tampered, tampered_fp = reencode(
                envelope, {1: json.dumps(changed, ensure_ascii=False,
                                         separators=(',', ':')).encode('utf-8')}
            )
            with self.assertRaises(HandoffError):
                self.accept(tampered, tampered_fp, store=self.fresh_store('position'))

        with self.subTest(tamper='bundle_total'):
            tampered_envelope = parse_envelope(payload)
            tampered_envelope['bundle']['total'] += 1
            tampered_envelope.pop('receipt_raws')
            tampered = encode_envelope(tampered_envelope)
            with self.assertRaises(HandoffError):
                self.accept(tampered, hashlib.sha256(tampered).hexdigest(),
                            store=self.fresh_store('total'))

    def test_same_recording_different_bytes_conflicts_at_source(self):
        payload, fingerprint_value = self.export()
        handoff_id = self.accept(payload, fingerprint_value)
        envelope = parse_envelope(payload)
        # 语义不变、字节异文（前导空白）：复核投影仍成立，但同源录制字节必须固定。
        variant_raw = b' ' + envelope['receipt_raws'][1]
        tampered, tampered_fp = reencode(envelope, {1: variant_raw}, bundle_id=999)
        with self.assertRaises(HandoffError):
            self.accept(tampered, tampered_fp, store=self.case['receiver'])
        self.assertEqual(read_handoff(self.case['receiver'], handoff_id)['issued'],
                         self.case['issued'])

    def test_same_frozen_row_different_facts_conflicts_at_source(self):
        payload, fingerprint_value = self.export()
        handoff_id = self.accept(payload, fingerprint_value)
        envelope = parse_envelope(payload)
        changed = json.loads(envelope['receipt_raws'][1])
        changed['rows'][0]['origin'] = '别处/A'
        variant_raw = json.dumps(changed, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        # 同步改写卷内投影，使交件自洽；冲突应来自同源冻结行核定而非投影核对。
        variant_envelope = parse_envelope(payload)
        variant_envelope['bundle']['lines'][0]['origin'] = '别处/A'
        variant_envelope.pop('receipt_raws')
        tampered, tampered_fp = reencode(variant_envelope, {1: variant_raw}, bundle_id=998)
        with self.assertRaises(HandoffError):
            self.accept(tampered, tampered_fp, store=self.case['receiver'])
        self.assertEqual(read_handoff(self.case['receiver'], handoff_id)['issued'],
                         self.case['issued'])

    def test_foreign_receipts_stay_in_handoff_domain(self):
        receiver = self.case['receiver']
        before = receiver.counts()
        handoff_id = self.accept(*self.export())
        # 普通签卷表完全不动：外来专用实收只进交接域。
        self.assertEqual(receiver.counts(), before)
        result = read_handoff(receiver, handoff_id)
        local_number = result['receipts'][1][0]
        with self.assertRaises(BadReceipt):
            load_receipts(receiver, [local_number])
        # 原签卷入口按本地证据继续办理，本地 1（另一录制）依旧可读。
        local = load_receipts(receiver, [1])
        self.assertEqual(local[0].evidence.rows[0].value, 90)


class TestHandoffOfflineReadback(HandoffFixtureCase):
    def test_readback_after_source_stores_go_offline(self):
        payload, fingerprint_value = self.export()
        handoff_id = self.accept(payload, fingerprint_value)
        expected = self.case['issued']

        # 门户、源收件库和源归档库全部停用（删除文件）后重开接班库。
        self.case['revisions'].path.unlink()
        self.case['origin_intake'].path.unlink()
        self.case['origin_archive'].path.unlink()
        reopened = IntakeStore(self.case['receiver'].path)
        reopened.initialize()
        result = read_handoff(reopened, handoff_id)
        self.assertEqual(result['source_id'], SOURCE_ID)
        self.assertEqual(result['issued'], expected)
        for source_receipt_id, (_local_id, raw) in result['receipts'].items():
            self.assertEqual(raw, self.origin_raw[source_receipt_id])
        self.assertIsNone(read_handoff(reopened, handoff_id + 100))
        self.assertIsNone(read_handoff(reopened, 0))
        # 同一交件重投复领原号。
        self.assertEqual(accept_handoff(reopened, SOURCE_ID, payload, fingerprint_value), handoff_id)


class TestHandoffWithCenterCase(CenterCase):
    def _receiver(self):
        store = IntakeStore(self.root / 'receiver.sqlite')
        store.initialize()
        return store

    def _issue_sample_bundle(self):
        revision = self.freeze_sample()
        first = self.receive(self.first_export(revision))
        second = self.receive(self.supplementary_export(revision))
        result = close(self.archive, self.intake, revision.revision_id, '甲',
                       [first.receipt_id, second.receipt_id])
        self.assertEqual(result.status, 'issued')
        return revision, result.issued

    def test_foreign_revision_receipt_only_lives_in_acknowledgment(self):
        revision, issued = self._issue_sample_bundle()
        # 另一版的实收进入同次首执请求，但不属于本卷实际采用集合。
        freeze_new = self.freeze_sample()
        self.assertNotEqual(freeze_new.revision_id, revision.revision_id)
        foreign_capture = export(self.revisions, self.recordings, freeze_new.revision_id, ('甲',))
        foreign = self.receive(foreign_capture)
        re_requested = close(self.archive, self.intake, revision.revision_id, '甲',
                             [*issued.bundle.receipt_ids, foreign.receipt_id])
        self.assertEqual(re_requested.status, 'issued')
        # 外版实收只留在首执请求里，卷内实际采用集合不变。
        request = json.loads(re_requested.issued.acknowledgment.request_key)
        self.assertIn(foreign.receipt_id, request[2])
        self.assertEqual(re_requested.issued.bundle.receipt_ids, issued.bundle.receipt_ids)

        payload, fingerprint_value = export_handoff(
            self.archive, self.intake, 'center', issued.bundle.bundle_id
        )
        envelope = parse_envelope(payload)
        self.assertEqual(sorted(envelope['receipt_raws']), list(issued.bundle.receipt_ids))
        receiver = self._receiver()
        handoff_id = accept_handoff(receiver, 'center', payload, fingerprint_value)
        result = read_handoff(receiver, handoff_id)
        self.assertEqual(result['issued'], issued)
        self.assertEqual(sorted(result['receipts']), list(issued.bundle.receipt_ids))

    def test_legal_zero_line_bundle_hands_over(self):
        revision = self.freeze_sample()
        captured = export(self.revisions, self.recordings, revision.revision_id, ('丙',))
        empty = self.receive(captured)
        result = close(self.archive, self.intake, revision.revision_id, '丙', [empty.receipt_id])
        self.assertEqual(result.status, 'issued')
        self.assertEqual(result.issued.bundle.lines, ())
        payload, fingerprint_value = export_handoff(
            self.archive, self.intake, 'center', result.issued.bundle.bundle_id
        )
        # 用来定空的原件须随卷。
        self.assertEqual(sorted(parse_envelope(payload)['receipt_raws']), [empty.receipt_id])
        receiver = self._receiver()
        handoff_id = accept_handoff(receiver, 'center', payload, fingerprint_value)
        read_back = read_handoff(receiver, handoff_id)
        self.assertEqual(read_back['issued'].bundle.lines, ())
        self.assertEqual(read_back['issued'].bundle.total, 0)
        self.assertEqual(
            read_back['receipts'][empty.receipt_id][1],
            self.intake.fetch_receipts([empty.receipt_id])[empty.receipt_id],
        )

    def test_write_failure_keeps_existing_handoffs_readable(self):
        revision, issued = self._issue_sample_bundle()
        receiver = self._receiver()
        payload, fingerprint_value = export_handoff(
            self.archive, self.intake, 'center', issued.bundle.bundle_id
        )
        handoff_id = accept_handoff(receiver, 'center', payload, fingerprint_value)
        # 第二来源的相同卷交件：在写入映射时触发存储故障。
        second_payload, second_fp = export_handoff(
            self.archive, self.intake, 'other-center', issued.bundle.bundle_id
        )
        import sqlite3
        with sqlite3.connect(receiver.path) as db:
            db.execute('''
                CREATE TRIGGER refuse_handoff BEFORE INSERT ON handoff_bundle_receipts
                BEGIN SELECT RAISE(ABORT, 'handoff storage unavailable'); END
            ''')
        with self.assertRaisesRegex(ValueError, 'handoff storage unavailable'):
            accept_handoff(receiver, 'other-center', second_payload, second_fp)
        # 事务回滚，既有交接成果和实收映射保持可读。
        self.assertEqual(read_handoff(receiver, handoff_id)['issued'], issued)
        self.assertEqual(receiver.handoff_counts(),
                         dict(handoffs=1, handoff_receipts=2, handoff_bundle_receipts=2))
        self.assertEqual(receiver.counts(), dict(receipts=0, known_rows=0, occurrences=0))
