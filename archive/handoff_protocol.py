"""离线交接交件的封装约定。

交件是版本 1 的 UTF-8 JSON 字节，独立于源库递送。原件字节以 base64 原样携带，
交件指纹为整段字节的 SHA-256 小写十六进制串，由办理人另行核对传递。
"""
import base64
import binascii
import hashlib
import json


HANDOFF_VERSION = 1


class HandoffError(ValueError):
    """坏交件、校验码不符或证据冲突。"""


def validate_source_id(source_id):
    if not isinstance(source_id, str) or not source_id.strip():
        raise HandoffError('source id must be stable nonempty UTF-8 text')
    try:
        source_id.encode('utf-8')
    except UnicodeEncodeError as exc:
        raise HandoffError('source id must be UTF-8 text') from exc
    return source_id


def fingerprint(payload):
    return hashlib.sha256(payload).hexdigest()


def _line_payload(line):
    return {
        'number': line.number,
        'area': line.area,
        'value': line.value,
        'origin': line.origin,
        'occurrences': [[recording_id, position] for recording_id, position in line.occurrences],
    }


def build_envelope(source_id, issued, raws_by_receipt):
    """按固定投影构造确定性的交件字典。"""
    bundle = issued.bundle
    receipts = []
    for source_receipt_id in sorted(raws_by_receipt):
        raw = raws_by_receipt[source_receipt_id]
        receipts.append({
            'source_receipt_id': source_receipt_id,
            'raw_base64': base64.b64encode(raw).decode('ascii'),
        })
    return {
        'version': HANDOFF_VERSION,
        'source_id': source_id,
        'bundle': {
            'bundle_id': bundle.bundle_id,
            'revision_id': bundle.revision_id,
            'target': bundle.target,
            'receipt_ids': list(bundle.receipt_ids),
            'lines': [_line_payload(line) for line in bundle.lines],
            'total': bundle.total,
        },
        'acknowledgment': {
            'acknowledgment_id': issued.acknowledgment.acknowledgment_id,
            'bundle_id': issued.acknowledgment.bundle_id,
            'request_key': issued.acknowledgment.request_key,
        },
        'receipts': receipts,
    }


def encode_envelope(envelope):
    return json.dumps(envelope, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode('utf-8')


def _unique_object(items):
    result = {}
    for key, value in items:
        if key in result:
            raise HandoffError(f'duplicate JSON key: {key}')
        result[key] = value
    return result


def _require_exact_keys(obj, keys, label):
    if not isinstance(obj, dict) or obj.keys() != keys:
        raise HandoffError(f'unexpected {label} fields')


def _identity(value):
    return type(value) is int and 0 < value < 2 ** 63


def _check_line(line):
    _require_exact_keys(line, {'number', 'area', 'value', 'origin', 'occurrences'}, 'bundle line')
    if not _identity(line['number']):
        raise HandoffError('invalid frozen row number in bundle')
    if not isinstance(line['area'], str) or not line['area'].strip():
        raise HandoffError('invalid bundle line area')
    if type(line['value']) is not int:
        raise HandoffError('bundle line reading must be an integer')
    if not isinstance(line['origin'], str) or not line['origin'].strip():
        raise HandoffError('invalid bundle line field origin')
    occurrences = line['occurrences']
    if not isinstance(occurrences, list) or not occurrences:
        raise HandoffError(f'bundle line {line["number"]} requires fixed download origins')
    pairs = []
    for pair in occurrences:
        if not isinstance(pair, list) or len(pair) != 2 or not all(_identity(item) for item in pair):
            raise HandoffError('invalid download origin in bundle')
        pairs.append((pair[0], pair[1]))
    if len(set(pairs)) != len(pairs):
        raise HandoffError(f'duplicate download origin on bundle line {line["number"]}')
    line['occurrences'] = pairs


def parse_envelope(payload):
    """解析并做结构校验，返回规范化后的交件字典。"""
    if not isinstance(payload, bytes):
        raise HandoffError('handoff payload must be bytes')
    try:
        envelope = json.loads(payload.decode('utf-8'), object_pairs_hook=_unique_object)
    except HandoffError:
        raise
    except (ValueError, RecursionError) as exc:
        raise HandoffError('invalid UTF-8 JSON handoff payload') from exc
    _require_exact_keys(envelope, {'version', 'source_id', 'bundle', 'acknowledgment', 'receipts'}, 'handoff')
    if type(envelope['version']) is not int or envelope['version'] != HANDOFF_VERSION:
        raise HandoffError('unsupported handoff payload version')
    validate_source_id(envelope['source_id'])

    bundle = envelope['bundle']
    _require_exact_keys(
        bundle, {'bundle_id', 'revision_id', 'target', 'receipt_ids', 'lines', 'total'}, 'bundle'
    )
    if not _identity(bundle['bundle_id']) or not _identity(bundle['revision_id']):
        raise HandoffError('invalid bundle or revision identity')
    if not isinstance(bundle['target'], str) or not bundle['target'].strip():
        raise HandoffError('invalid bundle target')
    receipt_ids = bundle['receipt_ids']
    if (not isinstance(receipt_ids, list) or not receipt_ids
            or any(not _identity(value) for value in receipt_ids)
            or len(set(receipt_ids)) != len(receipt_ids)):
        raise HandoffError('bundle must fix a nonempty set of adopted receipts')
    if type(bundle['total']) is not int or not isinstance(bundle['lines'], list):
        raise HandoffError('invalid bundle total or lines')
    lines = bundle['lines']
    for line in lines:
        _check_line(line)
    numbers = [line['number'] for line in lines]
    if len(set(numbers)) != len(numbers) or numbers != sorted(numbers):
        raise HandoffError('bundle lines must be distinct and ordered by frozen row number')
    if sum(line['value'] for line in lines) != bundle['total']:
        raise HandoffError('bundle total disagrees with its readings')

    acknowledgment = envelope['acknowledgment']
    _require_exact_keys(acknowledgment, {'acknowledgment_id', 'bundle_id', 'request_key'}, 'acknowledgment')
    if (not _identity(acknowledgment['acknowledgment_id'])
            or acknowledgment['bundle_id'] != bundle['bundle_id']):
        raise HandoffError('acknowledgment does not belong to the handed bundle')
    if not isinstance(acknowledgment['request_key'], str) or not acknowledgment['request_key']:
        raise HandoffError('invalid original request record')

    carried = envelope['receipts']
    if not isinstance(carried, list) or not carried:
        raise HandoffError('handoff must carry the adopted original receipts')
    raws = {}
    for item in carried:
        _require_exact_keys(item, {'source_receipt_id', 'raw_base64'}, 'handoff receipt')
        source_receipt_id = item['source_receipt_id']
        if not _identity(source_receipt_id):
            raise HandoffError('invalid source receipt identity')
        if source_receipt_id in raws:
            raise HandoffError(f'duplicate carried receipt {source_receipt_id}')
        encoded = item['raw_base64']
        if not isinstance(encoded, str):
            raise HandoffError('original receipt bytes must be base64 text')
        try:
            raws[source_receipt_id] = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HandoffError('original receipt bytes are not valid base64') from exc
    if set(raws) != set(receipt_ids):
        raise HandoffError('carried receipts do not match the receipts adopted by the bundle')
    envelope['receipt_raws'] = raws
    return envelope
