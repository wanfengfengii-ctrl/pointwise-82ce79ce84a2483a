from portal.capture import capture_export
from portal.export import export, replay_export
from portal.query import bind_query
from portal.recording import RecordingStore
from survey.areas import Area, UnknownArea
from survey.freeze import freeze
from survey.revision_store import RevisionStore
from survey.revisions import UnknownRevision, load_revision
from survey.rows import SurveyRow
from tests.fixtures import CenterCase


class TestEdition(CenterCase):
    def test_frozen_export(self):
        old = freeze(self.revisions, 'R', [self.rows[0]])
        bound = bind_query(self.revisions, old.revision_id, ('甲',))
        amended = SurveyRow(1, '甲', 11, '东段/A-复测')
        new = freeze(self.revisions, 'R', [amended], parent=old.revision_id)

        with self.subTest(case_id='V1.capture_binding'):
            record = self.recordings.save_recording(bound)
            actual = capture_export(record)
            self.assertEqual(actual.record.revision_id, old.revision_id)
            self.assertEqual(actual.record.scope, ('甲',))
            self.assertEqual(actual.record.hits, 1)
            self.assertEqual([(row.value, row.origin) for row in actual.record.rows], [(7, '东段/A')])
            self.assertEqual(actual.occurrences[0].position, 1)
            self.assertEqual(actual.occurrences[0].recording_id, record.recording_id)
            self.assertEqual(replay_export(self.recordings, record.recording_id), actual)

        old_export = export(self.revisions, self.recordings, old.revision_id, ('甲',))
        new_export = export(self.revisions, self.recordings, new.revision_id, ('甲',))
        self.assertEqual([old_export.record.rows[0].value, new_export.record.rows[0].value], [7, 11])
        self.assertEqual([old_export.record.rows[0].origin, new_export.record.rows[0].origin],
                         ['东段/A', '东段/A-复测'])
        self.assertEqual(new.parent_id, old.revision_id)

        with self.subTest(case_id='V1.parent_batch'):
            with self.assertRaises(ValueError):
                freeze(self.revisions, '另一批', [amended], parent=old.revision_id)
            self.assertEqual(self.revisions.count_revisions(), 2)
            self.assertEqual(load_revision(self.revisions, old.revision_id), old)

        with self.subTest(case_id='V1.reopen_init'):
            self.revisions.register_areas([Area('丁', '环线新段')])
            source = RevisionStore(self.revisions.path)
            recordings = RecordingStore(self.recordings.path)
            source.initialize()
            recordings.initialize()
            self.assertEqual(set(area.code for area in source.load_catalog().areas), {'甲', '乙', '丙', '丁'})
            self.assertEqual(source.count_revisions(), 2)
            self.assertEqual(recordings.count_recordings(), 3)
            self.assertEqual(load_revision(source, old.revision_id), old)
            self.assertEqual(load_revision(source, new.revision_id), new)
            self.assertEqual(replay_export(recordings, old_export.record.recording_id), old_export)
            self.assertNotIn('丁', bind_query(source, old.revision_id).scope)
            with self.assertRaises(UnknownArea):
                bind_query(source, old.revision_id, ('丁',))

    def test_unknown_area(self):
        revision = self.freeze_sample()
        self.first_export(revision)
        with self.assertRaises(UnknownArea):
            export(self.revisions, self.recordings, revision.revision_id, ('甲', '未登记'))
        self.assertEqual(self.recordings.count_recordings(), 1)
        with self.subTest(case_id='V2.unknown_revision'):
            with self.assertRaises(UnknownRevision):
                export(self.revisions, self.recordings, revision.revision_id + 999)
            self.assertEqual(self.recordings.count_recordings(), 1)
        with self.assertRaises(ValueError):
            export(self.revisions, self.recordings, revision.revision_id, ('甲',), returned_ids=(4,))
        self.assertEqual(self.recordings.count_recordings(), 1)

    def test_invalid_rows_do_not_freeze(self):
        for rows in (
            [SurveyRow(1, '甲', True, '现场')],
            [SurveyRow(1, '甲', None, '现场')],
            [SurveyRow(1, '甲', 7.0, '现场')],
            [SurveyRow(1, '甲', 7, '')],
            [SurveyRow(0, '甲', 7, '现场')],
            [SurveyRow(1, '未登记', 7, '现场')],
            [self.rows[0], self.rows[0]],
        ):
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):
                    freeze(self.revisions, 'R', rows)
                self.assertEqual(self.revisions.count_revisions(), 0)
        accepted = freeze(self.revisions, 'R', [SurveyRow(2, '甲', -3, '低位'), SurveyRow(1, '甲', 0, '基线')])
        self.assertEqual([row.value for row in accepted.rows], [0, -3])
