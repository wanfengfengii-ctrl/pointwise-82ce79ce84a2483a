from dataclasses import dataclass
from typing import Literal

from review.scopes import ScopeObservations, count_seen_in_scope


@dataclass(frozen=True)
class CompletenessResult:
    status: Literal['ready', 'pending', 'conflict']
    detail: str


def assess_completeness(observations: ScopeObservations):
    remaining_by_scope = {}
    for claim in observations.claims:
        scope = frozenset(claim.areas)
        seen = count_seen_in_scope(observations.rows, scope)
        remaining = claim.hits - seen
        if remaining < 0:
            return CompletenessResult('conflict', f'seen rows exceed exact hits for {sorted(scope)}')
        previous = remaining_by_scope.setdefault(scope, remaining)
        if previous != remaining:
            return CompletenessResult('conflict', f'different exact hits for {sorted(scope)}')

    for inner, inner_remaining in remaining_by_scope.items():
        for outer, outer_remaining in remaining_by_scope.items():
            if inner < outer and inner_remaining > outer_remaining:
                return CompletenessResult('conflict', 'a contained scope needs more unseen rows than its covering scope')

    for scope, remaining in remaining_by_scope.items():
        if observations.target in scope and remaining == 0:
            return CompletenessResult('ready', f'target covered by complete scope {sorted(scope)}')
    return CompletenessResult('pending', 'no complete scope establishes the target row count')
