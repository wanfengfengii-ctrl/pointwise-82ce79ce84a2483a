from dataclasses import dataclass

from intake.evidence import ScopeClaim
from intake.known_rows import KnownRow, collect_seen_rows


@dataclass(frozen=True)
class ScopeObservations:
    revision_id: int
    target: str
    receipt_ids: tuple[int, ...]
    claims: tuple[ScopeClaim, ...]
    rows: tuple[KnownRow, ...]


def build_scope_observations(revision_id, target, receipts):
    applicable = tuple(receipt for receipt in receipts if receipt.evidence.revision_id == revision_id)
    return ScopeObservations(
        revision_id,
        target,
        tuple(sorted({receipt.receipt_id for receipt in applicable})),
        tuple(receipt.evidence.claim for receipt in applicable),
        collect_seen_rows(applicable),
    )


def count_seen_in_scope(rows, areas):
    selected = frozenset(areas)
    return sum(row.area in selected for row in rows)
