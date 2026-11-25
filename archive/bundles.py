from dataclasses import asdict, dataclass
import json
import sqlite3

from review.rollup import BundleDraft, BundleLine


@dataclass(frozen=True)
class RegionBundle:
    bundle_id: int
    revision_id: int
    target: str
    receipt_ids: tuple[int, ...]
    lines: tuple[BundleLine, ...]
    total: int


def save_bundle(db, draft: BundleDraft):
    projection = json.dumps(
        {
            'version': 1,
            'receipts': draft.receipt_ids,
            'lines': [asdict(line) for line in draft.lines],
            'total': draft.total,
        },
        ensure_ascii=False,
        separators=(',', ':'),
    )
    cursor = db.execute(
        'INSERT INTO bundles(revision_id, target, projection) VALUES (?, ?, ?)',
        (draft.revision_id, draft.target, projection),
    )
    return RegionBundle(cursor.lastrowid, draft.revision_id, draft.target, draft.receipt_ids, draft.lines, draft.total)


def load_bundle(db, bundle_id):
    row = db.execute('SELECT revision_id, target, projection FROM bundles WHERE id = ?', (bundle_id,)).fetchone()
    if row is None:
        return None
    revision_id, target, projection = row
    payload = json.loads(projection)
    if payload['version'] != 1:
        raise sqlite3.DatabaseError('unsupported archived projection version')
    lines = tuple(
        BundleLine(
            line['number'], line['area'], line['value'], line['origin'],
            tuple(tuple(pair) for pair in line['occurrences']),
        )
        for line in payload['lines']
    )
    return RegionBundle(bundle_id, revision_id, target, tuple(payload['receipts']), lines, payload['total'])
