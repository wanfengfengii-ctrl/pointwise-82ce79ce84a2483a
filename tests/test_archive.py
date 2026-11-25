from archive.issue import close
from archive.readback import read
from archive.store import ArchiveStore
from intake.receipts import load_receipts, receive
from intake.store import IntakeStore
from portal.export import export
from tests.fixtures import CenterCase


class TestArchive(CenterCase):
    def test_read_after_reopen(self):
        revision = self.freeze_sample()
        captured = self.first_export(revision)
        first = self.receive(captured)
        next_receipt = self.receive(self.supplementary_export(revision))
        selected = [first.receipt_id, next_receipt.receipt_id]
        initial = close(self.archive, self.intake, revision.revision_id, '甲', selected)
        self.assertEqual(initial.status, 'issued')
        bundle_id = initial.issued.bundle.bundle_id
        self.assertEqual(initial.issued.acknowledgment.bundle_id, bundle_id)
        self.assertEqual(initial.issued.bundle.total, 29)

        with self.subTest(case_id='V7.fixed_origins'):
            late = export(self.revisions, self.recordings, revision.revision_id, ('甲',), returned_ids=(2,))
            self.receive(late)
            self.assertEqual(initial.issued.bundle.lines[1].occurrences, ((captured.record.recording_id, 2),))

        reopened = ArchiveStore(self.archive.path)
        intake = IntakeStore(self.intake.path)
        with self.subTest(case_id='V7.reopen_init'):
            reopened.initialize()
            intake.initialize()
            reopened.initialize()
            intake.initialize()
            self.assertEqual(intake.counts(), dict(receipts=3, known_rows=4, occurrences=5))
            self.assertEqual(load_receipts(intake, selected), (first, next_receipt))
            self.assertEqual(receive(intake, captured.raw), first)
            self.assertEqual(reopened.counts(), dict(bundles=1, acknowledgments=1, issued_requests=1))
        with self.subTest(case_id='V7.replay'):
            replay = close(reopened, intake, revision.revision_id, '甲', selected[::-1] + selected[:1])
            self.assertEqual(replay.status, 'issued')
            self.assertEqual(replay.issued, initial.issued)
            self.assert_archive_count(1)

        self.revisions.path.unlink()
        self.intake.path.unlink()
        retrieved = read(ArchiveStore(reopened.path), bundle_id)
        self.assertEqual(retrieved, initial.issued)
        self.assertEqual(retrieved.bundle.lines[1].occurrences, ((captured.record.recording_id, 2),))
        with self.subTest(case_id='V7.unknown_bundle'):
            self.assertIsNone(read(reopened, bundle_id + 100))

    def test_issue_retry(self):
        revision = self.freeze_sample()
        first = self.receive(self.first_export(revision))
        second = self.receive(self.supplementary_export(revision))
        selected = [first.receipt_id, second.receipt_id]
        self.archive_sql('''
            CREATE TRIGGER refuse_ack BEFORE INSERT ON acknowledgments
            BEGIN SELECT RAISE(ABORT, 'acknowledgment storage unavailable'); END
        ''')
        failed = close(self.archive, self.intake, revision.revision_id, '甲', selected)
        self.assertEqual(failed.status, 'write_failed')
        self.assertIn('acknowledgment storage unavailable', failed.detail)
        self.assertIsNone(failed.issued)
        reopened = ArchiveStore(self.archive.path)
        reopened.initialize()
        self.assertEqual(reopened.counts(), dict(bundles=0, acknowledgments=0, issued_requests=0))
        self.assertIsNone(read(reopened, 1))
        self.assertEqual(load_receipts(IntakeStore(self.intake.path), selected), (first, second))
        self.archive_sql('DROP TRIGGER refuse_ack')
        retried = close(reopened, IntakeStore(self.intake.path), revision.revision_id, '甲', selected)
        self.assertEqual(retried.status, 'issued')
        self.assertEqual(retried.issued.bundle.total, 29)
        self.assertEqual(read(reopened, retried.issued.bundle.bundle_id), retried.issued)
        self.assert_archive_count(1)
