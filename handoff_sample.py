import json
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.issue import close
from archive.store import ArchiveStore
from intake.receipts import load_receipts, receive
from intake.store import IntakeStore
from portal.export import export
from portal.recording import RecordingStore
from review.decisions import decide_closure
from survey.areas import Area
from survey.freeze import freeze
from survey.revision_store import RevisionStore
from survey.rows import SurveyRow
from tests.fixtures import independent_export, read_sample


def _issue(archive, intake, revision, target, numbers):
    result = close(archive, intake, revision, target, numbers)
    if result.status != "issued" or result.issued is None:
        raise RuntimeError(f"fixture could not issue {target}: {result}")
    return result.issued


def prepare(root):
    root = Path(root)
    revisions = RevisionStore(root / "portal.sqlite")
    recordings = RecordingStore(revisions.path)
    origin_intake = IntakeStore(root / "origin_intake.sqlite")
    origin_archive = ArchiveStore(root / "origin_archive.sqlite")
    receiver = IntakeStore(root / "receiver.sqlite")
    changed_intake = IntakeStore(root / "changed_intake.sqlite")
    changed_archive = ArchiveStore(root / "changed_archive.sqlite")
    for store in (revisions, recordings, origin_intake, origin_archive, receiver,
                  changed_intake, changed_archive):
        store.initialize()

    sample = read_sample("initial.json")
    rows = tuple(SurveyRow(**row) for row in sample["rows"])
    revisions.register_areas(Area(**area) for area in sample["areas"])
    edition = freeze(revisions, sample["batch"], rows).revision_id
    first = export(revisions, recordings, edition, sample["initial_scope"]).raw
    second = export(revisions, recordings, edition, **read_sample("supplement.json")).raw
    newer = freeze(revisions, sample["batch"], rows, parent=edition).revision_id
    other = export(revisions, recordings, newer, ("甲",), returned_ids=(1,)).raw
    original_ids = [receive(origin_intake, raw).receipt_id for raw in (first, second, other)]
    issued = _issue(origin_archive, origin_intake, edition, "甲", original_ids)
    extra = export(revisions, recordings, edition, **read_sample("supplement.json")).raw
    late = receive(origin_intake, extra)

    local = independent_export(edition, 1, ["甲"], 1, [SurveyRow(1, "甲", 90, "本地/A")])
    receive(receiver, local)
    for recording in range(11, 17):
        receive(receiver, independent_export(edition, recording, ["乙"], 0, []))
    reused = receive(receiver, second)

    changed_ids = [receive(changed_intake, raw).receipt_id for raw in (first, b" " + second, other)]
    _issue(changed_archive, changed_intake, edition, "甲", changed_ids)
    conflicting = _issue(changed_archive, changed_intake, edition, "乙", changed_ids)
    return dict(revisions=revisions, origin_intake=origin_intake, origin_archive=origin_archive,
                issued=issued, receiver=receiver, reused=reused, late=late,
                changed_intake=changed_intake, changed_archive=changed_archive, conflicting=conflicting)


def main():
    with TemporaryDirectory() as directory:
        case = prepare(directory)
        issue = case["issued"]
        lines = issue.bundle.lines
        original = json.loads(issue.acknowledgment.request_key)
        alternate = decide_closure(issue.bundle.revision_id, "甲", load_receipts(
            case["origin_intake"], [1, case["late"].receipt_id]
        )).draft
        local = load_receipts(case["receiver"], [1, 8])
        changed = load_receipts(case["changed_intake"], [2])[0].evidence.raw
        if (original != [1, "甲", [1, 2, 3]] or issue.bundle.receipt_ids != (1, 2)
                or issue.bundle.total != 29 or alternate is None or alternate.total != 29
                or lines[2].occurrences != ((2, 2),) or alternate.lines[2].occurrences != ((4, 2),)
                or local[0].evidence.rows[0].value != 90 or case["reused"] != local[1]
                or changed != b" " + local[1].evidence.raw
                or case["conflicting"].bundle.bundle_id != 2):
            raise RuntimeError("unexpected handoff fixture inputs")
        print(json.dumps(dict(total=29, requested=original[2], adopted=issue.bundle.receipt_ids,
                              original_occurrence=lines[2].occurrences,
                              late_occurrence=alternate.lines[2].occurrences,
                              receiver_value=90, receiver_reuse=local[1].receipt_id), ensure_ascii=False))


if __name__ == "__main__":
    main()
