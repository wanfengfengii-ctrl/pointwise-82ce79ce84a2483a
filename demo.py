import json
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.issue import close
from archive.readback import read
from archive.store import ArchiveStore
from intake.receipts import receive
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
        source.path.unlink()
        intake.path.unlink()
        reopened = ArchiveStore(archive.path)
        reopened.initialize()
        retrieved = read(reopened, bundle_id)
        if retrieved != result.issued or reopened.counts() != dict(bundles=1, acknowledgments=1, issued_requests=1):
            raise RuntimeError('offline readback did not return the committed issue')

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


if __name__ == '__main__':
    main()
