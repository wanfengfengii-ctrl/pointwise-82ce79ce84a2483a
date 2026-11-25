from dataclasses import dataclass

from survey.areas import AreaCatalog


@dataclass(frozen=True)
class SurveyRow:
    number: int
    area: str
    value: int
    origin: str


def validate_rows(catalog: AreaCatalog, rows):
    ordered = []
    numbers = set()
    for row in rows:
        if not isinstance(row, SurveyRow):
            raise ValueError('expected a SurveyRow')
        if type(row.number) is not int or not 0 < row.number < 2**63:
            raise ValueError('invalid frozen row number')
        if row.number in numbers:
            raise ValueError(f'duplicate frozen row {row.number}')
        catalog.require_area(row.area)
        if type(row.value) is not int:
            raise ValueError(f'row {row.number} requires an integer reading')
        if not isinstance(row.origin, str) or not row.origin.strip():
            raise ValueError(f'row {row.number} requires a field origin')
        row.origin.encode('utf-8')
        numbers.add(row.number)
        ordered.append(row)
    return tuple(sorted(ordered, key=lambda row: row.number))
