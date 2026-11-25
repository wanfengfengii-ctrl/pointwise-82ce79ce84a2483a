import json

from intake.known_rows import RowConflict, collect_seen_rows
from intake.receipts import BadReceipt, load_receipts, receive
from intake.store import IntakeStore
from intake.validate import EvidenceError
from portal.capture import capture_export
from portal.export import export, replay_export
from portal.query import bind_query
from survey.rows import SurveyRow
from tests.fixtures import CenterCase, independent_export


class TestIntake(CenterCase):
    def test_repeated_and_overlapping(self):
        revision = self.freeze_sample()
        first = self.first_export(revision)
        first_receipt = self.receive(first)
        with self.subTest(case_id='V3.source_count'):
            fixed, = load_receipts(self.intake, [first_receipt.receipt_id])
            self.assertEqual((first.record.hits, fixed.evidence.claim.hits, len(fixed.evidence.rows)), (3, 3, 2))
            self.assertEqual(fixed.evidence.raw, first.raw)
            self.assertEqual(fixed.evidence.claim.areas, ('甲',))

        with self.subTest(case_id='V3.replay'):
            reopened = IntakeStore(self.intake.path)
            reopened.initialize()
            replayed = receive(reopened, first.raw)
            self.assertEqual(replayed, first_receipt)
            self.assertEqual(reopened.counts(), dict(receipts=1, known_rows=2, occurrences=2))

        with self.subTest(case_id='V3.overlap_origins'):
            extra = export(self.revisions, self.recordings, revision.revision_id, ('甲',), returned_ids=(2, 3))
            extra_receipt = self.receive(extra)
            actual = load_receipts(self.intake, [extra_receipt.receipt_id, first_receipt.receipt_id, extra_receipt.receipt_id])
            self.assertEqual(actual, (first_receipt, extra_receipt))
            seen = collect_seen_rows(actual)
            self.assertEqual([(item.number, item.value) for item in seen], [(1, 7), (2, 11), (3, 11)])
            self.assertEqual(seen[1].occurrences, ((first.record.recording_id, 2), (extra.record.recording_id, 1)))
            self.assertEqual(seen[2].occurrences, ((extra.record.recording_id, 2),))
            self.assertEqual(self.intake.counts(), dict(receipts=2, known_rows=3, occurrences=4))

    def test_conflicting_origin(self):
        revision = self.freeze_sample()
        first = self.receive(self.first_export(revision))
        changed = SurveyRow(2, '甲', 99, '东段/B')
        bad = independent_export(revision.revision_id, 501, ('甲',), 3, (self.rows[2], changed))
        with self.assertRaises(RowConflict):
            receive(self.intake, bad)
        with self.subTest(case_id='V4.receipt_atomicity'):
            reopened = IntakeStore(self.intake.path)
            reopened.initialize()
            self.assertEqual(reopened.counts(), dict(receipts=1, known_rows=2, occurrences=2))
            self.assertEqual(load_receipts(reopened, [first.receipt_id]), (first,))
            with self.assertRaises(BadReceipt):
                load_receipts(reopened, [first.receipt_id + 1])
            retry = independent_export(revision.revision_id, 501, ('甲',), 3, self.rows[1:3])
            self.assertEqual(receive(reopened, retry).evidence.rows[0].value, 11)
            self.assertEqual(reopened.counts(), dict(receipts=2, known_rows=3, occurrences=4))
        changed_origin = SurveyRow(1, '甲', 7, '别处/A')
        with self.assertRaises(RowConflict):
            receive(self.intake, independent_export(revision.revision_id, 502, ('甲',), 3, (changed_origin,)))
        self.assertEqual(self.intake.counts(), dict(receipts=2, known_rows=3, occurrences=4))

    def test_recorded_hits_survive_the_same_limited_body(self):
        revision = self.freeze_sample()
        query = bind_query(self.revisions, revision.revision_id, ('甲',))
        ids = []
        for hits in (2, 3):
            record = self.recordings.save_recording(query, returned_ids=(1, 2), hits=hits)
            captured = capture_export(record)
            self.assertEqual(replay_export(self.recordings, record.recording_id), captured)
            ids.append(self.receive(captured).receipt_id)
        receipts = load_receipts(IntakeStore(self.intake.path), ids)
        self.assertEqual([receipt.evidence.claim.hits for receipt in receipts], [2, 3])
        self.assertEqual([[row.number for row in receipt.evidence.rows] for receipt in receipts], [[1, 2], [1, 2]])

    def test_bad_wire_is_rejected(self):
        revision = self.freeze_sample()
        captured = self.first_export(revision)
        for field, bad_value in (('number', True), ('value', 1.5), ('area', '乙'), ('origin', ''),
                                 ('recording', 999), ('position', 2)):
            with self.subTest(field=field):
                data = json.loads(captured.raw)
                data['rows'][0][field] = bad_value
                with self.assertRaises(EvidenceError):
                    receive(self.intake, json.dumps(data).encode())
        for field, bad_value in (('hits', -1), ('hits', False), ('scope', []), ('version', True)):
            with self.subTest(field=field, bad_value=bad_value):
                data = json.loads(captured.raw)
                data[field] = bad_value
                with self.assertRaises(EvidenceError):
                    receive(self.intake, json.dumps(data).encode())
        with self.assertRaises(EvidenceError):
            receive(self.intake, independent_export(revision.revision_id, 503, ('甲',), 3, self.rows[:3]))
        with self.assertRaises(EvidenceError):
            receive(self.intake, captured.raw.replace(b'"version":1', b'"version":1,"version":1'))
        self.assertEqual(self.intake.counts(), dict(receipts=0, known_rows=0, occurrences=0))
        original = self.receive(captured)
        with self.assertRaises(EvidenceError):
            receive(self.intake, captured.raw + b' ')
        self.assertEqual(load_receipts(self.intake, [original.receipt_id]), (original,))
