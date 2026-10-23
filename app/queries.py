"""Read queries shared by several pages."""

from collections.abc import Sequence

from sqlalchemy import and_
from sqlmodel import Session, col, func, select

from app.models import Attempt


def finished_attempts(session: Session, exam_id: int) -> Sequence[Attempt]:
    """History of an exam, newest first."""
    return session.exec(
        select(Attempt)
        .where(Attempt.exam_id == exam_id, col(Attempt.finished_at).is_not(None))
        .order_by(col(Attempt.finished_at).desc())
    ).all()


def last_finished_attempts(session: Session, exam_ids: Sequence[int]) -> dict[int, Attempt]:
    """Most recent finished attempt per exam, in one query."""
    latest = (
        select(Attempt.exam_id, func.max(Attempt.finished_at).label("finished_at"))
        .where(col(Attempt.exam_id).in_(exam_ids), col(Attempt.finished_at).is_not(None))
        .group_by(col(Attempt.exam_id))
        .subquery()
    )
    attempts = session.exec(
        select(Attempt).join(
            latest,
            and_(
                col(Attempt.exam_id) == latest.c.exam_id,
                col(Attempt.finished_at) == latest.c.finished_at,
            ),
        )
    )
    return {attempt.exam_id: attempt for attempt in attempts}
