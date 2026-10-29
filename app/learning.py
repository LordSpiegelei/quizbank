"""Local learning state on questions (CLAUDE.md §6): stars, disabled flags and the wrong pool.
The importer never touches it; resets are separate from deleting the history."""

from collections.abc import Callable, Iterable
from typing import Literal, get_args

from sqlalchemy import delete, update
from sqlmodel import Session, col

from app.models import Attempt, Question
from app.scoring import POOL_OUT

Flag = Literal["starred", "disabled"]
FLAGS: tuple[Flag, ...] = get_args(Flag)  # texts: flag.<name>.set / .unset / .state

# The question list's filters on the exam page, in display order.
QuestionFilter = Literal["all", "starred", "pool", "skipped"]
_FILTERS: dict[str, Callable[[Question], bool]] = {
    "all": lambda q: True,
    "starred": lambda q: q.starred,
    "pool": lambda q: q.in_wrong_pool,
    "skipped": lambda q: q.disabled,
}


def filter_counts(questions: Iterable[Question]) -> dict[str, int]:
    """How many questions each filter shows."""
    questions = list(questions)
    return {name: sum(map(keep, questions)) for name, keep in _FILTERS.items()}


def set_flag(session: Session, question: Question, flag: Flag, value: bool) -> None:
    """Set rather than flip, so a stale page can't toggle the wrong way."""
    setattr(question, flag, value)
    session.commit()


def remove_from_pool(session: Session, question: Question) -> None:
    """Manual "aus Pool entfernen": counts as learned, the streak starts over."""
    question.in_wrong_pool, question.pool_streak = POOL_OUT
    session.commit()


def reset_learning_state(session: Session, exam_id: int | None = None) -> None:
    """Clear stars, disabled flags and the wrong pool of one exam, or of all exams."""
    in_pool, streak = POOL_OUT
    stmt = update(Question).values(
        starred=False, disabled=False, in_wrong_pool=in_pool, pool_streak=streak
    )
    if exam_id is not None:
        stmt = stmt.where(col(Question.exam_id) == exam_id)
    session.exec(stmt)
    session.commit()


def delete_history(session: Session, exam_id: int) -> None:
    """Delete the finished attempts of an exam (answers cascade); an unfinished one stays and
    the wrong pool is left alone."""
    session.exec(
        delete(Attempt).where(
            col(Attempt.exam_id) == exam_id, col(Attempt.finished_at).is_not(None)
        )
    )
    session.commit()
