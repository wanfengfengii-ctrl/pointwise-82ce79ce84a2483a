from dataclasses import dataclass
import json
import sqlite3
from typing import Literal

from archive.acknowledgments import write_acknowledgment
from archive.bundles import save_bundle
from archive.readback import IssuedBundle, load_issued
from archive.store import ArchiveStore
from intake.receipts import BadReceipt, load_receipts
from intake.store import IntakeStore
from review.decisions import decide_closure


@dataclass(frozen=True)
class CloseResult:
    status: Literal['issued', 'pending', 'conflict', 'bad_receipt', 'invalid_request', 'write_failed']
    detail: str
    issued: IssuedBundle | None = None


def close(archive: ArchiveStore, intake: IntakeStore, revision_id, target, receipt_ids):
    if type(revision_id) is not int or not 0 < revision_id < 2**63:
        return CloseResult('invalid_request', 'invalid frozen revision number')
    if not isinstance(target, str) or not target.strip():
        return CloseResult('invalid_request', 'expected a target area code')
    try:
        target.encode('utf-8')
    except UnicodeEncodeError:
        return CloseResult('invalid_request', 'target area code is not UTF-8 text')
    try:
        receipts = load_receipts(intake, receipt_ids)
    except BadReceipt as exc:
        return CloseResult('bad_receipt', str(exc))

    request_key = json.dumps(
        [revision_id, target, [receipt.receipt_id for receipt in receipts]], separators=(',', ':')
    )
    decision = decide_closure(revision_id, target, receipts)
    if decision.draft is None:
        return CloseResult(decision.completeness.status, decision.completeness.detail)
    try:
        with archive.issue_transaction() as db:
            previous_id = archive.find_issued_request(db, request_key)
            if previous_id is None:
                bundle = save_bundle(db, decision.draft)
                acknowledgment = write_acknowledgment(db, bundle, request_key)
                archive.bind_issued_request(db, request_key, bundle.bundle_id)
                issued = IssuedBundle(bundle, acknowledgment)
            else:
                issued = load_issued(db, previous_id)
                if issued is None:
                    raise sqlite3.DatabaseError(f'missing issued bundle {previous_id}')
    except sqlite3.Error as exc:
        return CloseResult('write_failed', str(exc))
    return CloseResult('issued', decision.completeness.detail, issued)
