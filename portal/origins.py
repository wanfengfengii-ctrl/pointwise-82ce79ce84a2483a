from dataclasses import dataclass

from survey.rows import SurveyRow


@dataclass(frozen=True)
class ExportOccurrence:
    row: SurveyRow
    recording_id: int
    position: int


def build_export_occurrences(recording_id, rows):
    return tuple(ExportOccurrence(row, recording_id, position) for position, row in enumerate(rows, 1))
