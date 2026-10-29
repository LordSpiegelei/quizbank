"""Export an exam back to the import format (CLAUDE.md §6): the live questions in file order,
without learning state, so re-importing the file changes nothing."""

from typing import Any

from app.models import Exam, Question
from app.schemas import QUESTION_FIELDS, ExamFile


def _question(question: Question) -> dict[str, Any]:
    fields = QUESTION_FIELDS[question.type]
    return {"id": question.external_id} | {name: getattr(question, name) for name in fields}


def export_exam(exam: Exam) -> str:
    """The exam file as JSON; validated against the import schema on the way out."""
    exam_file = ExamFile.model_validate(
        {
            "format_version": 1,
            "course": exam.course.name,  # the import key, not the display name
            "title": exam.title,
            "time_limit_minutes": exam.time_limit_minutes,  # own values, not course defaults
            "pass_mark": exam.pass_mark,
            "multiple_scoring": exam.multiple_scoring,
            "questions": [_question(q) for q in exam.questions],
        }
    )
    return exam_file.model_dump_json(indent=2, exclude_none=True) + "\n"
