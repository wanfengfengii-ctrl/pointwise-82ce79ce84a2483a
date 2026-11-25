from survey.revision_store import RevisionStore
from survey.revisions import FrozenRevision, resolve_parent_revision
from survey.rows import validate_rows


def freeze(store: RevisionStore, batch, rows, parent=None):
    if not isinstance(batch, str) or not batch.strip():
        raise ValueError('batch must be nonempty text')
    batch.encode('utf-8')
    parent_revision = resolve_parent_revision(store, batch, parent)
    parent_id = None if parent_revision is None else parent_revision.revision_id
    catalog = store.load_catalog()
    if not catalog.areas:
        raise ValueError('register areas before freezing a revision')
    ordered = validate_rows(catalog, rows)
    revision_id = store.append_revision(batch, parent_id, catalog, ordered)
    return FrozenRevision(revision_id, batch, parent_id, catalog, ordered)
