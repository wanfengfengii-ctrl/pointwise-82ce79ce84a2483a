from dataclasses import dataclass

from survey.areas import AreaCatalog
from survey.revision_store import RevisionStore
from survey.rows import SurveyRow


class UnknownRevision(LookupError):
    pass


@dataclass(frozen=True)
class FrozenRevision:
    revision_id: int
    batch: str
    parent_id: int | None
    catalog: AreaCatalog
    rows: tuple[SurveyRow, ...]


def load_revision(store: RevisionStore, revision_id):
    stored = store.fetch_revision(revision_id)
    if stored is None:
        raise UnknownRevision(f'unknown frozen revision: {revision_id!r}')
    batch, parent_id, catalog, rows = stored
    return FrozenRevision(revision_id, batch, parent_id, catalog, rows)


def resolve_parent_revision(store: RevisionStore, batch, parent_id):
    if parent_id is None:
        return None
    parent = load_revision(store, parent_id)
    if parent.batch != batch:
        raise ValueError('parent revision belongs to another batch')
    return parent
