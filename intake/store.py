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
                CREATE TABLE IF NOT EXISTS handoff_receipts (
                    source_id TEXT NOT NULL, source_receipt_id INTEGER NOT NULL,
                    local_receipt_id INTEGER NOT NULL, recording_id INTEGER NOT NULL,
                    revision_id INTEGER NOT NULL, raw BLOB NOT NULL,
                    PRIMARY KEY (source_id, source_receipt_id),
                    UNIQUE (source_id, recording_id)
                );
                CREATE TABLE IF NOT EXISTS handoffs (
                    id INTEGER PRIMARY KEY, source_id TEXT NOT NULL,
                    source_bundle_id INTEGER NOT NULL, fingerprint TEXT NOT NULL UNIQUE,
                    payload BLOB NOT NULL,
                    UNIQUE (source_id, source_bundle_id)
                );
                CREATE TABLE IF NOT EXISTS handoff_bundle_receipts (
                    handoff_id INTEGER NOT NULL, source_receipt_id INTEGER NOT NULL,
                    local_receipt_id INTEGER NOT NULL,
                    PRIMARY KEY (handoff_id, source_receipt_id),
                    FOREIGN KEY (handoff_id) REFERENCES handoffs(id)
                );
                COMMIT;
            ''')

    @contextmanager
    def transaction(self):
        with closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            yield db

    @contextmanager
    def read_snapshot(self):
        with closing(self._connect()) as db, db:
            db.execute('BEGIN')
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

    def find_handoff(self, db, fingerprint_value):
        row = db.execute(
            'SELECT id FROM handoffs WHERE fingerprint = ?', (fingerprint_value,)
        ).fetchone()
        return None if row is None else row[0]

    def find_source_bundle(self, db, source_id, source_bundle_id):
        row = db.execute(
            'SELECT id, fingerprint FROM handoffs WHERE source_id = ? AND source_bundle_id = ?',
            (source_id, source_bundle_id),
        ).fetchone()
        return None if row is None else (row[0], row[1])

    def load_handoff(self, db, handoff_id):
        row = db.execute(
            'SELECT source_id, payload FROM handoffs WHERE id = ?', (handoff_id,)
        ).fetchone()
        if row is None:
            return None
        bindings = db.execute(
            'SELECT source_receipt_id, local_receipt_id FROM handoff_bundle_receipts '
            'WHERE handoff_id = ? ORDER BY source_receipt_id',
            (handoff_id,),
        ).fetchall()
        return row[0], row[1], tuple(bindings)

    def load_source_receipts(self, db, source_id):
        return db.execute(
            'SELECT source_receipt_id, local_receipt_id, recording_id, revision_id, raw '
            'FROM handoff_receipts WHERE source_id = ?',
            (source_id,),
        ).fetchall()

    def find_source_recording(self, db, source_id, recording_id):
        return db.execute(
            'SELECT source_receipt_id, raw FROM handoff_receipts WHERE source_id = ? AND recording_id = ?',
            (source_id, recording_id),
        ).fetchone()

    def find_handoff_receipt(self, db, source_id, source_receipt_id):
        return db.execute(
            'SELECT local_receipt_id, raw FROM handoff_receipts '
            'WHERE source_id = ? AND source_receipt_id = ?',
            (source_id, source_receipt_id),
        ).fetchone()

    def find_local_receipt_by_raw(self, db, raw):
        row = db.execute('SELECT id FROM receipts WHERE raw = ?', (raw,)).fetchone()
        return None if row is None else row[0]

    def handoff_watermark(self, db):
        row = db.execute(
            'SELECT MAX(used) FROM ('
            'SELECT COALESCE(MAX(id), 0) AS used FROM receipts '
            'UNION ALL SELECT COALESCE(MAX(local_receipt_id), 0) FROM handoff_receipts)'
        ).fetchone()
        return row[0]

    def local_id_taken(self, db, local_receipt_id):
        row = db.execute(
            'SELECT 1 FROM receipts WHERE id = ? '
            'UNION ALL SELECT 1 FROM handoff_receipts WHERE local_receipt_id = ? LIMIT 1',
            (local_receipt_id, local_receipt_id),
        ).fetchone()
        return row is not None

    def save_handoff_receipt(self, db, source_id, source_receipt_id, local_receipt_id,
                             recording_id, revision_id, raw):
        db.execute(
            'INSERT INTO handoff_receipts VALUES (?, ?, ?, ?, ?, ?)',
            (source_id, source_receipt_id, local_receipt_id, recording_id, revision_id, raw),
        )

    def save_handoff(self, db, source_id, source_bundle_id, fingerprint_value, payload):
        cursor = db.execute(
            'INSERT INTO handoffs(source_id, source_bundle_id, fingerprint, payload) VALUES (?, ?, ?, ?)',
            (source_id, source_bundle_id, fingerprint_value, payload),
        )
        return cursor.lastrowid

    def save_handoff_binding(self, db, handoff_id, source_receipt_id, local_receipt_id):
        db.execute(
            'INSERT INTO handoff_bundle_receipts VALUES (?, ?, ?)',
            (handoff_id, source_receipt_id, local_receipt_id),
        )

    def handoff_counts(self):
        with closing(self._connect()) as db:
            row = db.execute('''
                SELECT (SELECT COUNT(*) FROM handoffs),
                       (SELECT COUNT(*) FROM handoff_receipts),
                       (SELECT COUNT(*) FROM handoff_bundle_receipts)
            ''').fetchone()
        return dict(zip(('handoffs', 'handoff_receipts', 'handoff_bundle_receipts'), row))
