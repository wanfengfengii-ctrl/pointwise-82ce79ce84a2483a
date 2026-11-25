from dataclasses import dataclass
import sqlite3

from archive.acknowledgments import IssueAcknowledgment, load_acknowledgment
from archive.bundles import RegionBundle, load_bundle
from archive.handoff_protocol import build_envelope, encode_envelope, fingerprint, validate_source_id
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


def export_handoff(archive: ArchiveStore, intake, source_id, bundle_id):
    """导出原卷、首执及卷内实际采用原件的可独立递送交件。

    外版选件只保留在首执请求里；随件正文集合恰为本卷实际采用的同版实收。
    未知卷或无法从源收件库取齐原件时抛 ValueError。
    """
    validate_source_id(source_id)
    if type(bundle_id) is not int or not 0 < bundle_id < 2**63:
        raise ValueError(f'unknown bundle {bundle_id!r}')
    with archive.read_snapshot() as db:
        issued = load_issued(db, bundle_id)
    if issued is None:
        raise ValueError(f'unknown bundle {bundle_id}')
    adopted = issued.bundle.receipt_ids
    stored = intake.fetch_receipts(adopted)
    if set(stored) != set(adopted):
        missing = sorted(set(adopted) - set(stored))
        raise ValueError(f'source intake cannot provide original receipts {missing}')
    envelope = build_envelope(validate_source_id(source_id), issued, stored)
    payload = encode_envelope(envelope)
    return payload, fingerprint(payload)
