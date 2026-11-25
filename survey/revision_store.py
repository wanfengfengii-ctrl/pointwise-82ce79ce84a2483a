from contextlib import closing
from pathlib import Path
import sqlite3

from survey.areas import Area, AreaCatalog
from survey.rows import SurveyRow


class RevisionStore:
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
                CREATE TABLE IF NOT EXISTS areas (
                    code TEXT PRIMARY KEY, name TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS revisions (
                    id INTEGER PRIMARY KEY, batch TEXT NOT NULL, parent_id INTEGER,
                    UNIQUE (id, batch),
                    FOREIGN KEY (parent_id, batch) REFERENCES revisions(id, batch)
                );
                CREATE TABLE IF NOT EXISTS revision_areas (
                    revision_id INTEGER NOT NULL REFERENCES revisions(id),
                    code TEXT NOT NULL, name TEXT NOT NULL,
                    PRIMARY KEY (revision_id, code)
                );
                CREATE TABLE IF NOT EXISTS revision_rows (
                    revision_id INTEGER NOT NULL REFERENCES revisions(id),
                    number INTEGER NOT NULL, area TEXT NOT NULL,
                    reading TEXT NOT NULL, origin TEXT NOT NULL,
                    PRIMARY KEY (revision_id, number),
                    FOREIGN KEY (revision_id, area) REFERENCES revision_areas(revision_id, code)
                );
                COMMIT;
            ''')

    def register_areas(self, areas):
        catalog = AreaCatalog(tuple(areas))
        with closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            for area in catalog.areas:
                existing = db.execute('SELECT name FROM areas WHERE code = ?', (area.code,)).fetchone()
                if existing is None:
                    db.execute('INSERT INTO areas VALUES (?, ?)', (area.code, area.name))
                elif existing[0] != area.name:
                    raise ValueError(f'area {area.code} already has a different name')
        return self.load_catalog()

    def load_catalog(self):
        with closing(self._connect()) as db:
            entries = db.execute('SELECT code, name FROM areas ORDER BY code').fetchall()
        return AreaCatalog(tuple(Area(*entry) for entry in entries))

    def append_revision(self, batch, parent_id, catalog, rows):
        with closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            result = db.execute('INSERT INTO revisions(batch, parent_id) VALUES (?, ?)', (batch, parent_id))
            revision_id = result.lastrowid
            db.executemany(
                'INSERT INTO revision_areas VALUES (?, ?, ?)',
                ((revision_id, area.code, area.name) for area in catalog.areas),
            )
            db.executemany(
                'INSERT INTO revision_rows VALUES (?, ?, ?, ?, ?)',
                ((revision_id, row.number, row.area, str(row.value), row.origin) for row in rows),
            )
        return revision_id

    def fetch_revision(self, revision_id):
        if type(revision_id) is not int or not 0 < revision_id < 2**63:
            return None
        with closing(self._connect()) as db, db:
            db.execute('BEGIN')
            header = db.execute('SELECT batch, parent_id FROM revisions WHERE id = ?', (revision_id,)).fetchone()
            if header is None:
                return None
            areas = db.execute(
                'SELECT code, name FROM revision_areas WHERE revision_id = ? ORDER BY code', (revision_id,)
            ).fetchall()
            rows = db.execute(
                'SELECT number, area, reading, origin FROM revision_rows WHERE revision_id = ? ORDER BY number',
                (revision_id,),
            ).fetchall()
        catalog = AreaCatalog(tuple(Area(*area) for area in areas))
        ordered = tuple(SurveyRow(number, area, int(value), origin) for number, area, value, origin in rows)
        return header[0], header[1], catalog, ordered

    def count_revisions(self):
        with closing(self._connect()) as db:
            return db.execute('SELECT COUNT(*) FROM revisions').fetchone()[0]
