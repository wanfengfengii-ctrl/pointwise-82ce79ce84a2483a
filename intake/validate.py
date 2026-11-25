from intake.evidence import Evidence
from intake.evidence import EvidenceError as EvidenceError


V1_BODY_LIMIT = 2


def _valid_text(value):
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        value.encode('utf-8')
    except UnicodeEncodeError:
        return False
    return True


def _valid_identity(value):
    return type(value) is int and 0 < value < 2**63


def validate_evidence(evidence: Evidence):
    if not _valid_identity(evidence.recording_id) or not _valid_identity(evidence.revision_id):
        raise EvidenceError('invalid recording or frozen revision identity')
    scope = evidence.claim.areas
    if not scope or any(not _valid_text(area) for area in scope):
        raise EvidenceError('invalid query scope')
    areas = frozenset(scope)
    if len(areas) != len(scope):
        raise EvidenceError('duplicate query area')
    hits = evidence.claim.hits
    if type(hits) is not int or hits < 0:
        raise EvidenceError('invalid exact hit count')
    if len(evidence.rows) > V1_BODY_LIMIT:
        raise EvidenceError('body exceeds the version 1 row limit')
    numbers = set()
    for position, row in enumerate(evidence.rows, 1):
        if not _valid_identity(row.number) or row.number in numbers:
            raise EvidenceError('invalid or repeated frozen row number')
        if not _valid_text(row.area) or row.area not in areas:
            raise EvidenceError(f'row {row.number} is outside the query scope')
        if type(row.value) is not int or not _valid_text(row.origin):
            raise EvidenceError(f'invalid reading or field origin on row {row.number}')
        if not _valid_identity(row.recording_id) or row.recording_id != evidence.recording_id:
            raise EvidenceError(f'row {row.number} refers to another recording')
        if type(row.position) is not int or row.position != position:
            raise EvidenceError(f'invalid occurrence position on row {row.number}')
        numbers.add(row.number)
    return evidence
