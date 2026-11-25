from dataclasses import dataclass

from intake.evidence import Evidence, EvidenceError
from intake.store import IntakeStore


class RowConflict(EvidenceError):
    pass


@dataclass(frozen=True)
class KnownRow:
    revision_id: int
    number: int
    area: str
    value: int
    origin: str
    occurrences: tuple[tuple[int, int], ...]


def register_occurrences(store: IntakeStore, db, receipt_id, evidence: Evidence):
    revision_id = evidence.revision_id
    for row in evidence.rows:
        facts = (row.area, str(row.value), row.origin)
        previous = store.find_known_row(db, revision_id, row.number)
        if previous is None:
            store.save_known_row(db, revision_id, row.number, facts)
        elif previous != facts:
            raise RowConflict(f'frozen revision {revision_id} row {row.number} conflicts with an earlier receipt')
        store.save_occurrence(db, receipt_id, row.position, revision_id, row.number)


def collect_seen_rows(receipts):
    facts_by_identity = {}
    origins_by_identity = {}
    for receipt in receipts:
        evidence = receipt.evidence
        for occurrence in evidence.rows:
            identity = (evidence.revision_id, occurrence.number)
            facts = (occurrence.area, occurrence.value, occurrence.origin)
            previous = facts_by_identity.setdefault(identity, facts)
            if previous != facts:
                raise RowConflict(f'inconsistent stored rows for revision and line {identity}')
            origins_by_identity.setdefault(identity, set()).add((occurrence.recording_id, occurrence.position))
    return tuple(
        KnownRow(*identity, *facts_by_identity[identity], tuple(sorted(origins_by_identity[identity])))
        for identity in sorted(facts_by_identity)
    )
