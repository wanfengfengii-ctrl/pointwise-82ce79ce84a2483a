import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.issue import close
from archive.readback import export_handoff, read
from archive.store import ArchiveStore
from intake.receipts import accept_handoff, read_handoff, receive
from intake.store import IntakeStore
from portal.export import export
from portal.recording import RecordingStore
from survey.areas import Area
from survey.freeze import freeze
from survey.revision_store import RevisionStore
from survey.rows import SurveyRow


def _sample(name):
    path = Path(__file__).resolve().parent / 'samples' / name
    return json.loads(path.read_text(encoding='utf-8'))


def _projection(issued):
    return {
        'numbers': [line.number for line in issued.bundle.lines],
        'values': [line.value for line in issued.bundle.lines],
        'field_origins': [line.origin for line in issued.bundle.lines],
        'occurrences': [[list(pair) for pair in line.occurrences] for line in issued.bundle.lines],
        'total': issued.bundle.total,
    }


def main():
    initial = _sample('initial.json')
    supplement = _sample('supplement.json')
    with TemporaryDirectory() as directory:
        root = Path(directory)
        source = RevisionStore(root / 'source.sqlite')
        recordings = RecordingStore(source.path)
        intake = IntakeStore(root / 'intake.sqlite')
        archive = ArchiveStore(root / 'archive.sqlite')
        for store in (source, recordings, intake, archive):
            store.initialize()
        source.register_areas(Area(**area) for area in initial['areas'])
        revision = freeze(source, initial['batch'], (SurveyRow(**row) for row in initial['rows']))
        target = initial['target']

        first = export(source, recordings, revision.revision_id, initial['initial_scope'])
        first_receipt = receive(intake, first.raw)
        pending = close(archive, intake, revision.revision_id, target, [first_receipt.receipt_id])
        before = archive.counts()
        if pending.status != 'pending' or pending.issued is not None or any(before.values()):
            raise RuntimeError('the limited first delivery must leave the regional archive pending')

        second = export(source, recordings, revision.revision_id, **supplement)
        second_receipt = receive(intake, second.raw)
        selected = [first_receipt.receipt_id, second_receipt.receipt_id]
        result = close(archive, intake, revision.revision_id, target, selected)
        if result.status != 'issued' or result.issued is None:
            raise RuntimeError(f'supplement did not close the regional archive: {result}')
        expected = {
            'numbers': [1, 2, 3],
            'values': [7, 11, 11],
            'field_origins': ['东段/A', '东段/B', '东段/C'],
            'occurrences': [[[first.record.recording_id, 1]], [[first.record.recording_id, 2]],
                            [[second.record.recording_id, 2]]],
            'total': 29,
        }
        actual = _projection(result.issued)
        if actual != expected:
            raise RuntimeError(f'unexpected regional projection: {actual}')
        bundle_id = result.issued.bundle.bundle_id
        replay = close(archive, intake, revision.revision_id, target, selected[::-1] + selected[:1])
        if replay.issued != result.issued:
            raise RuntimeError('equivalent request did not return the first bundle and acknowledgment')

        # 先验证归档库自身的离线读回（仅关门户，保留源收件库与归档库供交接导出）。
        source.path.unlink()
        reopened = ArchiveStore(archive.path)
        reopened.initialize()
        retrieved = read(reopened, bundle_id)
        if retrieved != result.issued or reopened.counts() != dict(bundles=1, acknowledgments=1, issued_requests=1):
            raise RuntimeError('offline readback did not return the committed issue')

        # 再用仍可读的源收件库、源归档库导出离线交接交件，接班库接纳并在断源后按号读回。
        _demonstrate_offline_handoff(root, intake, archive, bundle_id, result.issued)

        observation = {
            'case_id': 'V6',
            'input': {
                'revision': revision.revision_id,
                'target': target,
                'deliveries': [json.loads(item.raw) for item in (first, second)],
            },
            'expected': {'first_status': 'pending', 'first_bundle_count': 0, 'projection': expected},
            'actual': {'first_status': pending.status, 'first_bundle_count': before['bundles'], 'projection': actual},
            'downstream_expected': {'bundle': bundle_id, 'projection': expected, 'same_first_acknowledgment': True},
            'downstream_actual': {
                'bundle': retrieved.bundle.bundle_id,
                'projection': _projection(retrieved),
                'same_first_acknowledgment': retrieved.acknowledgment == result.issued.acknowledgment,
            },
            'code_paths': ['portal/capture.py', 'intake/receipts.py', 'review/completeness.py',
                           'review/rollup.py', 'archive/issue.py', 'archive/readback.py'],
            'oracle': '甲区声明三条。取冻结号 1、2、3，按两份正文的实际位置核对出处。整数手算 7+11+11=29。',
        }
        print('POINTWISE_SCENARIO ' + json.dumps(observation, ensure_ascii=False, separators=(',', ':')))


def _demonstrate_offline_handoff(root, source_intake, source_archive, bundle_id, issued):
    source_id = 'day-shift-center-A'
    portal_path = root / 'source.sqlite'
    # 源库导出原卷、首执及实际采用的原件，并以源发校验码固定交件。
    payload, fingerprint_value = export_handoff(source_archive, source_intake, source_id, bundle_id)
    receiver = IntakeStore(root / 'receiver.sqlite')
    receiver.initialize()
    handoff_id = accept_handoff(receiver, source_id, payload, fingerprint_value)
    repeated_id = accept_handoff(receiver, source_id, payload, fingerprint_value)
    if repeated_id != handoff_id:
        raise RuntimeError('repeated handoff did not return the original handoff number')

    # 停用门户、源收件库和源归档库后，重开接班库按号读回。
    source_intake.path.unlink()
    source_archive.path.unlink()
    portal_offline = not portal_path.exists()
    reopened = IntakeStore(receiver.path)
    reopened.initialize()
    handoff = read_handoff(reopened, handoff_id)
    if handoff['issued'] != issued:
        raise RuntimeError('offline handoff readback did not return the original bundle')
    bundle = handoff['issued'].bundle
    acknowledgment = handoff['issued'].acknowledgment
    handoff_view = {
        'case_id': 'HANDOFF',
        'source_id': source_id,
        'fingerprint': fingerprint_value,
        'handoff_id': handoff_id,
        'repeat_returns_same_id': repeated_id == handoff_id,
        'source_offline': portal_offline and not source_intake.path.exists()
                          and not source_archive.path.exists(),
        'bundle': {
            'source_bundle_id': bundle.bundle_id,
            'revision_id': bundle.revision_id,
            'target': bundle.target,
            'ordered_numbers': [line.number for line in bundle.lines],
            'field_origins': [line.origin for line in bundle.lines],
            'download_origins': [[list(pair) for pair in line.occurrences] for line in bundle.lines],
            'total': bundle.total,
        },
        'acknowledgment': {
            'request': json.loads(acknowledgment.request_key),
            'note': '首执照录整次请求；外版选件只留在原首执里，随件仅为实际采用的同版实收。',
        },
        'receipt_mapping': {
            str(source_receipt_id): {
                'local_receipt_id': local_receipt_id,
                'bytes_sha256': hashlib.sha256(raw).hexdigest(),
            }
            for source_receipt_id, (local_receipt_id, raw) in sorted(handoff['receipts'].items())
        },
        'readback_matches_original': handoff['issued'] == issued,
    }
    print('POINTWISE_HANDOFF ' + json.dumps(handoff_view, ensure_ascii=False, separators=(',', ':')))


if __name__ == '__main__':
    main()
