from dataclasses import dataclass

from review.scopes import ScopeObservations


@dataclass(frozen=True)
class BundleLine:
    number: int
    area: str
    value: int
    origin: str
    occurrences: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class BundleDraft:
    revision_id: int
    target: str
    receipt_ids: tuple[int, ...]
    lines: tuple[BundleLine, ...]
    total: int


def build_rollup(observations: ScopeObservations):
    target_rows = sorted(
        (row for row in observations.rows if row.area == observations.target), key=lambda row: row.number
    )
    lines = tuple(BundleLine(row.number, row.area, row.value, row.origin, row.occurrences) for row in target_rows)
    return BundleDraft(
        observations.revision_id,
        observations.target,
        observations.receipt_ids,
        lines,
        sum(line.value for line in lines),
    )
