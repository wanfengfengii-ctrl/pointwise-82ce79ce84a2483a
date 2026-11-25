from dataclasses import dataclass
import json

from portal.origins import ExportOccurrence, build_export_occurrences
from portal.recording import ExportRecord


@dataclass(frozen=True)
class CapturedExport:
    record: ExportRecord
    occurrences: tuple[ExportOccurrence, ...]
    raw: bytes


def capture_export(record: ExportRecord):
    occurrences = build_export_occurrences(record.recording_id, record.rows)
    payload = {
        'version': 1,
        'recording': record.recording_id,
        'revision': record.revision_id,
        'scope': record.scope,
        'hits': record.hits,
        'rows': [
            {
                'number': occurrence.row.number,
                'area': occurrence.row.area,
                'value': occurrence.row.value,
                'origin': occurrence.row.origin,
                'recording': occurrence.recording_id,
                'position': occurrence.position,
            }
            for occurrence in occurrences
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return CapturedExport(record, occurrences, raw)
