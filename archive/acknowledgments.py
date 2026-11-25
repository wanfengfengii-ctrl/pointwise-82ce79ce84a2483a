from dataclasses import dataclass

from archive.bundles import RegionBundle


@dataclass(frozen=True)
class IssueAcknowledgment:
    acknowledgment_id: int
    bundle_id: int
    request_key: str


def write_acknowledgment(db, bundle: RegionBundle, request_key):
    cursor = db.execute(
        'INSERT INTO acknowledgments(bundle_id, request_key) VALUES (?, ?)', (bundle.bundle_id, request_key)
    )
    return IssueAcknowledgment(cursor.lastrowid, bundle.bundle_id, request_key)


def load_acknowledgment(db, bundle_id):
    row = db.execute('''
        SELECT a.id, a.bundle_id, a.request_key
        FROM acknowledgments AS a
        JOIN issued_requests AS r ON r.bundle_id = a.bundle_id AND r.request_key = a.request_key
        WHERE a.bundle_id = ?
    ''', (bundle_id,)).fetchone()
    return None if row is None else IssueAcknowledgment(*row)
