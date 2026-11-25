from dataclasses import dataclass
import sqlite3

from archive.acknowledgments import IssueAcknowledgment, load_acknowledgment
from archive.bundles import RegionBundle, load_bundle
from archive.store import ArchiveStore


@dataclass(frozen=True)
class IssuedBundle:
    bundle: RegionBundle
    acknowledgment: IssueAcknowledgment


def load_issued(db, bundle_id):
    bundle = load_bundle(db, bundle_id)
    if bundle is None:
        return None
    acknowledgment = load_acknowledgment(db, bundle_id)
    if acknowledgment is None:
        raise sqlite3.DatabaseError(f'missing acknowledgment or request for issued bundle {bundle_id}')
    return IssuedBundle(bundle, acknowledgment)


def read(store: ArchiveStore, bundle_id):
    if type(bundle_id) is not int or not 0 < bundle_id < 2**63:
        return None
    with store.read_snapshot() as db:
        return load_issued(db, bundle_id)
