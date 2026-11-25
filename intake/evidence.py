from dataclasses import dataclass
import json


class EvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class ScopeClaim:
    areas: tuple[str, ...]
    hits: int


@dataclass(frozen=True)
class EvidenceOccurrence:
    number: int
    area: str
    value: int
    origin: str
    recording_id: int
    position: int


@dataclass(frozen=True)
class Evidence:
    recording_id: int
    revision_id: int
    claim: ScopeClaim
    rows: tuple[EvidenceOccurrence, ...]
    raw: bytes


def _unique_object(items):
    result = {}
    for key, value in items:
        if key in result:
            raise EvidenceError(f'duplicate JSON key: {key}')
        result[key] = value
    return result


def _require_keys(obj, keys):
    if not isinstance(obj, dict) or obj.keys() != keys:
        raise EvidenceError('unexpected export fields')


def decode_evidence(raw):
    if not isinstance(raw, bytes):
        raise EvidenceError('export must be UTF-8 bytes')
    try:
        data = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object)
    except EvidenceError:
        raise
    except (ValueError, RecursionError) as exc:
        raise EvidenceError('invalid UTF-8 JSON export') from exc
    _require_keys(data, {'version', 'recording', 'revision', 'scope', 'hits', 'rows'})
    if type(data['version']) is not int or data['version'] != 1:
        raise EvidenceError('unsupported export version')
    if not isinstance(data['scope'], list) or not isinstance(data['rows'], list):
        raise EvidenceError('export scope and rows must be arrays')
    rows = []
    for row in data['rows']:
        _require_keys(row, {'number', 'area', 'value', 'origin', 'recording', 'position'})
        rows.append(EvidenceOccurrence(
            row['number'], row['area'], row['value'], row['origin'], row['recording'], row['position']
        ))
    claim = ScopeClaim(tuple(data['scope']), data['hits'])
    return Evidence(data['recording'], data['revision'], claim, tuple(rows), raw)
