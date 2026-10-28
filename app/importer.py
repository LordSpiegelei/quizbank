"""Import validated exam files into the database (idempotent, matched by course/title/id)."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from app.config import Settings
from app.models import Course, Exam, Question, utcnow
from app.schemas import QUESTION_MODELS, ExamFile, ExamFileError, QuestionIn, parse_exam

logger = logging.getLogger(__name__)

# Columns only some question types use; reset to the model default when a type doesn't set
# them, so changing a question's type leaves no stale data behind.
_FIELDS_PER_TYPE = [set(model.model_fields) for model in QUESTION_MODELS]
_TYPE_SPECIFIC_FIELDS = set.union(*_FIELDS_PER_TYPE) - set.intersection(*_FIELDS_PER_TYPE)


@dataclass
class ImportResult:
    exam_id: int
    created: int
    updated: int
    removed: int
    changed: bool


def _apply(obj: Any, values: dict[str, Any]) -> bool:
    """Set attributes that differ; return whether anything changed."""
    changed = False
    for key, value in values.items():
        if getattr(obj, key) != value:
            setattr(obj, key, value)
            changed = True
    return changed


def _question_values(question: QuestionIn, position: int) -> dict[str, Any]:
    defaults = {
        name: Question.model_fields[name].get_default(call_default_factory=True)
        for name in _TYPE_SPECIFIC_FIELDS
    }
    return defaults | question.model_dump(exclude={"id"}) | {"position": position}


def import_exam(
    session: Session, exam_file: ExamFile, source_file: str | None = None
) -> ImportResult:
    exam = session.exec(
        select(Exam)
        .join(Course)
        .where(Course.name == exam_file.course, Exam.title == exam_file.title)
    ).first()
    exam_created = exam is None
    if exam is None:
        course = session.exec(select(Course).where(Course.name == exam_file.course)).first()
        exam = Exam(course=course or Course(name=exam_file.course), title=exam_file.title)
        session.add(exam)

    exam_values = exam_file.model_dump(
        include={"time_limit_minutes", "pass_mark", "multiple_scoring"}
    )
    changed = _apply(exam, exam_values | {"source_file": source_file}) or exam_created

    created = updated = 0
    existing = {q.external_id: q for q in exam.questions}
    for position, question in enumerate(exam_file.questions):
        values = _question_values(question, position)
        row = existing.pop(question.id, None)
        if row is None:
            exam.questions.append(Question(external_id=question.id, **values))
            created += 1
        elif _apply(row, values):
            updated += 1

    # The file is authoritative: questions no longer in it are deleted (with their answers).
    for row in existing.values():
        exam.questions.remove(row)

    changed = changed or bool(created or updated or existing)
    if changed:
        exam.imported_at = utcnow()
    session.flush()
    result = ImportResult(exam.id, created, updated, len(existing), changed)
    session.commit()
    return result


def comes_back_on_restart(settings: Settings, exam: Exam) -> bool:
    """Whether auto-import would re-create the exam: its file is still in QUESTIONS_DIR."""
    if not settings.auto_import or exam.source_file is None:
        return False
    return (settings.questions_dir / Path(exam.source_file).name).is_file()


def import_directory(session: Session, directory: Path) -> dict[str, ImportResult | list[str]]:
    """Import every *.json in `directory`; maps file name to result or validation errors."""
    imports: dict[str, ImportResult | list[str]] = {}
    for path in sorted(directory.glob("*.json")):
        try:
            exam_file = parse_exam(path.read_bytes())
        except ExamFileError as exc:
            logger.warning("Skipping %s: %s", path.name, exc)
            imports[path.name] = exc.errors
            continue
        imports[path.name] = import_exam(session, exam_file, source_file=path.name)
        logger.info("Imported %s (%s / %s)", path.name, exam_file.course, exam_file.title)
    return imports
