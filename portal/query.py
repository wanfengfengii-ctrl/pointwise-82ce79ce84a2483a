from dataclasses import dataclass

from survey.revisions import FrozenRevision, load_revision


@dataclass(frozen=True)
class BoundQuery:
    revision: FrozenRevision
    scope: tuple[str, ...]


def bind_query(revisions, revision_id, scope=None):
    revision = load_revision(revisions, revision_id)
    return BoundQuery(revision, revision.catalog.resolve_scope(scope))


def select_matching_rows(query: BoundQuery):
    scope = frozenset(query.scope)
    return tuple(row for row in query.revision.rows if row.area in scope)
