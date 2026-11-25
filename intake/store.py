from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3


class IntakeStore:
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
                CREATE TABLE IF NOT EXISTS receipts (
                    id INTEGER PRIMARY KEY, recording_id INTEGER NOT NULL UNIQUE,
                    revision_id INTEGER NOT NULL, raw BLOB NOT NULL,
                    UNIQUE (id, revision_id)
                );
                CREATE TABLE IF NOT EXISTS known_rows (
                    revision_id INTEGER NOT NULL, number INTEGER NOT NULL,
                    area TEXT NOT NULL, reading TEXT NOT NULL, origin TEXT NOT NULL,
                    PRIMARY KEY (revision_id, number)
                );
                CREATE TABLE IF NOT EXISTS occurrences (
                    receipt_id INTEGER NOT NULL, position INTEGER NOT NULL,
                    revision_id INTEGER NOT NULL, number INTEGER NOT NULL,
                    PRIMARY KEY (receipt_id, position),
                    FOREIGN KEY (receipt_id, revision_id) REFERENCES receipts(id, revision_id),
                    FOREIGN KEY (revision_id, number) REFERENCES known_rows(revision_id, number)
                );
                COMMIT;
            ''')

    @contextmanager
    def transaction(self):
        with closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            yield db

    def find_receipt(self, db, recording_id):
        return db.execute('SELECT id, raw FROM receipts WHERE recording_id = ?', (recording_id,)).fetchone()

    def save_receipt(self, db, recording_id, revision_id, raw):
        cursor = db.execute(
            'INSERT INTO receipts(recording_id, revision_id, raw) VALUES (?, ?, ?)',
            (recording_id, revision_id, raw),
        )
        return cursor.lastrowid

    def fetch_receipts(self, ids):
        records = {}
        with closing(self._connect()) as db, db:
            db.execute('BEGIN')
            for receipt_id in ids:
                row = db.execute('SELECT raw FROM receipts WHERE id = ?', (receipt_id,)).fetchone()
                if row is not None:
                    records[receipt_id] = row[0]
        return records

    def find_known_row(self, db, revision_id, number):
        return db.execute(
            'SELECT area, reading, origin FROM known_rows WHERE revision_id = ? AND number = ?',
            (revision_id, number),
        ).fetchone()

    def save_known_row(self, db, revision_id, number, facts):
        db.execute('INSERT INTO known_rows VALUES (?, ?, ?, ?, ?)', (revision_id, number, *facts))

    def save_occurrence(self, db, receipt_id, position, revision_id, number):
        db.execute('INSERT INTO occurrences VALUES (?, ?, ?, ?)', (receipt_id, position, revision_id, number))

    def counts(self):
        with closing(self._connect()) as db:
            row = db.execute('''
                SELECT (SELECT COUNT(*) FROM receipts),
                       (SELECT COUNT(*) FROM known_rows),
                       (SELECT COUNT(*) FROM occurrences)
            ''').fetchone()
        return dict(zip(('receipts', 'known_rows', 'occurrences'), row))
