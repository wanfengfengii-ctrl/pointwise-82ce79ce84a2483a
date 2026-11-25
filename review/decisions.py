from dataclasses import dataclass

from review.completeness import CompletenessResult, assess_completeness
from review.rollup import BundleDraft, build_rollup
from review.scopes import build_scope_observations


@dataclass(frozen=True)
class ClosureDecision:
    completeness: CompletenessResult
    draft: BundleDraft | None


def decide_closure(revision_id, target, receipts):
    observations = build_scope_observations(revision_id, target, receipts)
    assessment = assess_completeness(observations)
    if assessment.status != 'ready':
        return ClosureDecision(assessment, None)
    return ClosureDecision(assessment, build_rollup(observations))
