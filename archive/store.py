from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3


class ArchiveStore:
    def __init__(self, path):
        self.path = Path(path)

    def _connect(self):
        connection = sqlite3.connect(self.path)
        connection.execute('PRAGMA foreign_keys = ON')
        return connection

    def initialize(self):
        with closing(self._connect()) as db:
            db.executescript('''
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS bundles (
                    id INTEGER PRIMARY KEY, revision_id INTEGER NOT NULL,
                    target TEXT NOT NULL, projection TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS acknowledgments (
                    id INTEGER PRIMARY KEY, bundle_id INTEGER NOT NULL UNIQUE REFERENCES bundles(id),
                    request_key TEXT NOT NULL UNIQUE, UNIQUE(bundle_id, request_key)
                );
                CREATE TABLE IF NOT EXISTS issued_requests (
                    request_key TEXT PRIMARY KEY, bundle_id INTEGER NOT NULL UNIQUE,
                    FOREIGN KEY (bundle_id, request_key) REFERENCES acknowledgments(bundle_id, request_key)
                );
                COMMIT;
            ''')

    @contextmanager
    def issue_transaction(self):
        with closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            yield db

    @contextmanager
    def read_snapshot(self):
        with closing(self._connect()) as db, db:
            db.execute('BEGIN')
            yield db

    def find_issued_request(self, db, request_key):
        row = db.execute('SELECT bundle_id FROM issued_requests WHERE request_key = ?', (request_key,)).fetchone()
        return None if row is None else row[0]

    def bind_issued_request(self, db, request_key, bundle_id):
        db.execute('INSERT INTO issued_requests VALUES (?, ?)', (request_key, bundle_id))

    def counts(self):
        with self.read_snapshot() as db:
            row = db.execute('''
                SELECT (SELECT COUNT(*) FROM bundles),
                       (SELECT COUNT(*) FROM acknowledgments),
                       (SELECT COUNT(*) FROM issued_requests)
            ''').fetchone()
        return dict(zip(('bundles', 'acknowledgments', 'issued_requests'), row))
