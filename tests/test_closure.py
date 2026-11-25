from archive.issue import close
from archive.readback import read
from intake.receipts import receive
from portal.export import export
from survey.freeze import freeze
from survey.rows import SurveyRow
from tests.fixtures import CenterCase, independent_export


class TestClosure(CenterCase):
    def test_truncated_scope(self):
        revision = self.freeze_sample()
        first = self.receive(self.first_export(revision))
        result = close(self.archive, self.intake, revision.revision_id, '甲', [first.receipt_id])
        self.assertEqual(result.status, 'pending')
        self.assertIsNone(result.issued)
        self.assert_archive_count(0)

        with self.subTest(case_id='V5.cross_revision'):
            amended = freeze(self.revisions, 'R', self.rows, parent=revision.revision_id)
            wrong = self.receive(self.supplementary_export(amended))
            right = self.receive(self.supplementary_export(revision))
            result = close(self.archive, self.intake, revision.revision_id, '甲', [wrong.receipt_id, first.receipt_id])
            self.assertEqual(result.status, 'pending')
            self.assertIsNone(result.issued)
            self.assert_archive_count(0)

        with self.subTest(case_id='V5.bad_receipt'):
            result = close(self.archive, self.intake, revision.revision_id, '甲',
                           [first.receipt_id, right.receipt_id, 9999])
            self.assertEqual(result.status, 'bad_receipt')
            self.assertIsNone(result.issued)
            self.assert_archive_count(0)
        result = close(self.archive, self.intake, revision.revision_id, '甲',
                       [first.receipt_id, wrong.receipt_id, right.receipt_id])
        self.assertEqual(result.status, 'issued')
        self.assertEqual(result.issued.bundle.receipt_ids, (first.receipt_id, right.receipt_id))
        self.assertEqual(result.issued.bundle.total, 29)

    def test_different_hits(self):
        revision = self.freeze_sample()
        limited = self.receive(self.first_export(revision))
        complete_revision = freeze(self.revisions, 'R', self.rows[:2], parent=revision.revision_id)
        complete = self.receive(self.first_export(complete_revision))
        self.assertEqual(limited.evidence.rows[0].value, complete.evidence.rows[0].value)
        self.assertEqual(limited.evidence.rows[1].value, complete.evidence.rows[1].value)
        self.assertEqual((complete.evidence.claim.hits, limited.evidence.claim.hits), (2, 3))
        ready = close(self.archive, self.intake, complete_revision.revision_id, '甲', [complete.receipt_id])
        pending = close(self.archive, self.intake, revision.revision_id, '甲', [limited.receipt_id])
        self.assertEqual(ready.status, 'issued')
        self.assertEqual(ready.issued.bundle.total, 18)
        self.assertEqual(pending.status, 'pending')
        self.assertIsNone(pending.issued)
        self.assert_archive_count(1)

    def test_overlap_completion(self):
        revision = self.freeze_sample()
        initial = self.first_export(revision)
        first = self.receive(initial)
        self.assertEqual(close(self.archive, self.intake, revision.revision_id, '甲', [first.receipt_id]).status, 'pending')
        self.assert_archive_count(0)
        subsequent = self.supplementary_export(revision)
        later = self.receive(subsequent)
        self.assertEqual([row.number for row in later.evidence.rows], [4, 3])
        overlap = export(self.revisions, self.recordings, revision.revision_id, ('甲',), returned_ids=(2, 3))
        overlapping = self.receive(overlap)
        result = close(self.archive, self.intake, revision.revision_id, '甲', [later.receipt_id, first.receipt_id])
        self.assertEqual(result.status, 'issued')
        self.assertEqual([line.number for line in result.issued.bundle.lines], [1, 2, 3])
        self.assertEqual([line.value for line in result.issued.bundle.lines], [7, 11, 11])
        self.assertEqual(result.issued.bundle.total, 29)
        self.assertEqual(result.issued.bundle.lines[2].occurrences, ((subsequent.record.recording_id, 2),))
        self.assertEqual(result.issued.bundle.lines[1].occurrences, ((initial.record.recording_id, 2),))
        self.assert_archive_count(1)

        with self.subTest(case_id='V6.target_order'):
            result = close(self.archive, self.intake, revision.revision_id, '甲',
                           [overlapping.receipt_id, later.receipt_id, first.receipt_id, later.receipt_id])
            self.assertEqual(result.status, 'issued')
            lines = result.issued.bundle.lines
            self.assertEqual([(line.number, line.area, line.value, line.origin) for line in lines],
                             [(1, '甲', 7, '东段/A'), (2, '甲', 11, '东段/B'), (3, '甲', 11, '东段/C')])
            self.assertEqual([line.occurrences for line in lines], [
                ((initial.record.recording_id, 1),),
                ((initial.record.recording_id, 2), (overlap.record.recording_id, 1)),
                ((subsequent.record.recording_id, 2), (overlap.record.recording_id, 2)),
            ])
            self.assertEqual(result.issued.bundle.total, 29)
            self.assertEqual(read(self.archive, result.issued.bundle.bundle_id), result.issued)
            self.assert_archive_count(2)

    def test_conflicting_counts(self):
        revision = self.freeze_sample()
        one = self.receive(self.first_export(revision))
        two = receive(self.intake, independent_export(revision.revision_id, 510, ('甲',), 2, self.rows[:2]))
        result = close(self.archive, self.intake, revision.revision_id, '甲', [one.receipt_id, two.receipt_id])
        self.assertEqual(result.status, 'conflict')
        self.assertIsNone(result.issued)
        self.assert_archive_count(0)

    def test_covering_claims(self):
        revision = self.freeze_sample()
        for offset, (hits, expected, count) in enumerate(((5, 'pending', 0), (3, 'conflict', 0), (4, 'issued', 1))):
            with self.subTest(hits=hits):
                recording = 600 + 3 * offset
                bodies = [
                    (('甲', '乙', '丙'), hits, self.rows[:2]),
                    (('甲', '乙', '丙'), hits, self.rows[2:]),
                    (('乙',), 1, self.rows[3:]),
                ]
                selected = [receive(self.intake, independent_export(revision.revision_id, recording + index,
                            scope, declared, rows)).receipt_id for index, (scope, declared, rows) in enumerate(bodies)]
                result = close(self.archive, self.intake, revision.revision_id, '甲', selected)
                self.assertEqual(result.status, expected)
                self.assert_archive_count(count)
                if expected == 'issued':
                    self.assertEqual(result.issued.bundle.total, 29)
                    self.assertEqual([line.number for line in result.issued.bundle.lines], [1, 2, 3])
                else:
                    self.assertIsNone(result.issued)

    def test_empty_target(self):
        revision = self.freeze_sample()
        empty_body = receive(self.intake, independent_export(revision.revision_id, 700, ('丙',), 1, ()))
        result = close(self.archive, self.intake, revision.revision_id, '丙', [empty_body.receipt_id])
        self.assertEqual(result.status, 'pending')
        self.assertIsNone(result.issued)
        self.assert_archive_count(0)
        empty_scope = self.receive(export(self.revisions, self.recordings, revision.revision_id, ('丙',)))
        result = close(self.archive, self.intake, revision.revision_id, '丙', [empty_scope.receipt_id])
        self.assertEqual(result.status, 'issued')
        self.assertEqual(result.issued.bundle.lines, ())
        self.assertEqual(result.issued.bundle.total, 0)
        self.assert_archive_count(1)

    def test_signed_integer_rollup(self):
        revision = freeze(self.revisions, 'signed', [SurveyRow(1, '甲', 2**70, '现场/甲1'),
                                                    SurveyRow(2, '甲', -13, '现场/甲2')])
        first = self.receive(self.first_export(revision))
        result = close(self.archive, self.intake, revision.revision_id, '甲', [first.receipt_id])
        self.assertEqual(result.status, 'issued')
        retrieved = read(self.archive, result.issued.bundle.bundle_id)
        self.assertEqual([line.value for line in retrieved.bundle.lines], [1180591620717411303424, -13])
        self.assertEqual(retrieved.bundle.total, 1180591620717411303411)
