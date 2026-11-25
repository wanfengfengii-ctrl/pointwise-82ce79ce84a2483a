from contextlib import closing
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3

from portal.query import BoundQuery, select_matching_rows
from survey.rows import SurveyRow


BODY_LIMIT = 2


@dataclass(frozen=True)
class ExportRecord:
    recording_id: int
    revision_id: int
    scope: tuple[str, ...]
    hits: int
    rows: tuple[SurveyRow, ...]


class RecordingStore:
    def __init__(self, path):
        self.path = Path(path)

    def initialize(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('''
                CREATE TABLE IF NOT EXISTS recordings (
                    id INTEGER PRIMARY KEY, revision_id INTEGER NOT NULL,
                    scope TEXT NOT NULL, hits TEXT NOT NULL, body BLOB NOT NULL
                )
            ''')

    def save_recording(self, query: BoundQuery, *, returned_ids=None, hits=None):
        matches = select_matching_rows(query)
        if returned_ids is None:
            body = matches[:BODY_LIMIT]
        else:
            ids = tuple(returned_ids)
            if len(ids) > BODY_LIMIT or any(type(number) is not int for number in ids):
                raise ValueError('invalid selection for the limited body')
            if len(set(ids)) != len(ids):
                raise ValueError('a row may occur only once in a recording')
            by_number = {row.number: row for row in matches}
            try:
                body = tuple(by_number[number] for number in ids)
            except KeyError as exc:
                raise ValueError(f'row did not match the bound query: {exc.args[0]}') from exc
        if hits is None:
            hits = len(matches)
        if type(hits) is not int or hits < 0:
            raise ValueError('source hit count must be a nonnegative integer')
        raw = json.dumps(
            [[row.number, row.area, row.value, row.origin] for row in body],
            ensure_ascii=False, separators=(',', ':'),
        ).encode('utf-8')
        with closing(sqlite3.connect(self.path)) as db, db:
            saved = db.execute(
                'INSERT INTO recordings(revision_id, scope, hits, body) VALUES (?, ?, ?, ?)',
                (query.revision.revision_id, json.dumps(query.scope, ensure_ascii=False), str(hits), raw),
            )
            recording_id = saved.lastrowid
        return ExportRecord(recording_id, query.revision.revision_id, query.scope, hits, body)

    def load_recording(self, recording_id):
        if type(recording_id) is not int or not 0 < recording_id < 2**63:
            raise LookupError(f'unknown recording: {recording_id!r}')
        with closing(sqlite3.connect(self.path)) as db:
            record = db.execute(
                'SELECT revision_id, scope, hits, body FROM recordings WHERE id = ?', (recording_id,)
            ).fetchone()
        if record is None:
            raise LookupError(f'unknown recording: {recording_id}')
        revision_id, scope, hits, raw = record
        rows = tuple(SurveyRow(*values) for values in json.loads(raw))
        return ExportRecord(recording_id, revision_id, tuple(json.loads(scope)), int(hits), rows)

    def count_recordings(self):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute('SELECT COUNT(*) FROM recordings').fetchone()[0]
