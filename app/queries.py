"""Read queries shared by several pages."""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Integer, and_
from sqlalchemy.orm import selectinload
from sqlmodel import Session, col, func, select

from app.models import Answer, Attempt, Course, Exam, Question


def nav_courses(session: Session) -> Sequence[tuple[Course, int]]:
    """Every course with its number of exams, by displayed name (for the sidebar)."""
    return session.exec(
        select(Course, func.count(col(Exam.id)))
        .outerjoin(Exam, col(Exam.course_id) == Course.id)
        .group_by(col(Course.id))
        .order_by(func.casefold(Course.label))
    ).all()


def _latest_finished(exam_ids: Sequence[int] | None = None) -> Any:
    """Subquery: per exam, when its latest attempt finished."""
    stmt = select(Attempt.exam_id, func.max(Attempt.finished_at).label("finished_at")).where(
        col(Attempt.finished_at).is_not(None)
    )
    if exam_ids is not None:
        stmt = stmt.where(col(Attempt.exam_id).in_(exam_ids))
    return stmt.group_by(col(Attempt.exam_id)).subquery()


def _join_latest(latest: Any) -> Any:
    return and_(
        col(Attempt.exam_id) == latest.c.exam_id, col(Attempt.finished_at) == latest.c.finished_at
    )


def recent_exams(session: Session, limit: int = 4) -> Sequence[tuple[Exam, Attempt]]:
    """The exams practised last, each with its latest finished attempt (for the overview)."""
    latest = _latest_finished()
    return session.exec(
        select(Exam, Attempt)
        .join(latest, col(Exam.id) == latest.c.exam_id)
        .join(Attempt, _join_latest(latest))
        .options(selectinload(Exam.course))
        .order_by(latest.c.finished_at.desc())
        .limit(limit)
    ).all()


def live_questions(session: Session, attempt: Attempt) -> dict[int, Question]:
    """The attempt's questions that still exist, by id, in one query instead of one each."""
    ids = [a.question_id for a in attempt.answers if a.question_id is not None]
    return {q.id: q for q in session.exec(select(Question).where(col(Question.id).in_(ids)))}


def topic_counts(session: Session, exam_id: int) -> Sequence[tuple[str, int, int]]:
    """(topic, answered, correct) over the exam's finished attempts, by snapshot topic.
    is_correct as stored by finish(): unanswered and partly right are False; open
    questions stay None until self-assessed and are left out (CLAUDE.md §10)."""
    topic = func.json_extract(Answer.snapshot, "$.topic")
    return session.exec(
        select(topic, func.count(), func.sum(Answer.is_correct, type_=Integer))
        .join(Attempt)
        .where(
            Attempt.exam_id == exam_id,
            col(Attempt.finished_at).is_not(None),
            col(Answer.is_correct).is_not(None),
        )
        .group_by(topic)
    ).all()


def finished_attempts(session: Session, exam_id: int) -> Sequence[Attempt]:
    """History of an exam, newest first."""
    return session.exec(
        select(Attempt)
        .where(Attempt.exam_id == exam_id, col(Attempt.finished_at).is_not(None))
        .order_by(col(Attempt.finished_at).desc())
    ).all()


def last_finished_attempts(session: Session, exam_ids: Sequence[int]) -> dict[int, Attempt]:
    """Most recent finished attempt per exam, in one query."""
    latest = _latest_finished(exam_ids)
    attempts = session.exec(select(Attempt).join(latest, _join_latest(latest)))
    return {attempt.exam_id: attempt for attempt in attempts}
