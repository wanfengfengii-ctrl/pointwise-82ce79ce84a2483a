from portal.capture import capture_export
from portal.query import bind_query
from portal.recording import RecordingStore
from survey.revision_store import RevisionStore


def export(revisions: RevisionStore, recordings: RecordingStore, revision_id, scope=None, returned_ids=None):
    query = bind_query(revisions, revision_id, scope)
    record = recordings.save_recording(query, returned_ids=returned_ids)
    return capture_export(record)


def replay_export(recordings: RecordingStore, recording_id):
    return capture_export(recordings.load_recording(recording_id))
