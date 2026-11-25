from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from archive.store import ArchiveStore
from intake.receipts import receive
from intake.store import IntakeStore
from portal.export import export
from portal.recording import RecordingStore
from survey.areas import Area
from survey.freeze import freeze
from survey.revision_store import RevisionStore
from survey.rows import SurveyRow


SAMPLES = Path(__file__).resolve().parents[1] / 'samples'


def read_sample(name):
    return json.loads((SAMPLES / name).read_text(encoding='utf-8'))


def independent_export(revision, recording, scope, hits, rows):
    body = []
    for position, row in enumerate(rows, 1):
        body.append(dict(number=row.number, area=row.area, value=row.value, origin=row.origin,
                         recording=recording, position=position))
    payload = dict(version=1, recording=recording, revision=revision, scope=scope, hits=hits, rows=body)
    return json.dumps(payload, ensure_ascii=False).encode('utf-8')


class CenterCase(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.revisions = RevisionStore(self.root / 'source.sqlite')
        self.recordings = RecordingStore(self.revisions.path)
        self.intake = IntakeStore(self.root / 'intake.sqlite')
        self.archive = ArchiveStore(self.root / 'archive.sqlite')
        for store in (self.revisions, self.recordings, self.intake, self.archive):
            store.initialize()
        self.sample = read_sample('initial.json')
        self.catalog = self.revisions.register_areas(Area(**entry) for entry in self.sample['areas'])
        self.rows = tuple(SurveyRow(**entry) for entry in self.sample['rows'])

    def freeze_sample(self):
        return freeze(self.revisions, self.sample['batch'], self.rows)

    def first_export(self, revision):
        return export(self.revisions, self.recordings, revision.revision_id, self.sample['initial_scope'])

    def supplementary_export(self, revision):
        return export(self.revisions, self.recordings, revision.revision_id, **read_sample('supplement.json'))

    def receive(self, captured):
        return receive(self.intake, captured.raw)

    def assert_archive_count(self, count):
        self.assertEqual(self.archive.counts(), dict(bundles=count, acknowledgments=count, issued_requests=count))

    def archive_sql(self, statement):
        with closing(sqlite3.connect(self.archive.path)) as db, db:
            db.execute(statement)
