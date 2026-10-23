"""Attempt lifecycle: start with frozen question snapshots, save answers, finish and score."""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any, Literal

from sqlmodel import Session, col, select

from app.models import Answer, Attempt, Exam, Question, utcnow
from app.schemas import QUESTION_MODELS, MultipleScoring
from app.scoring import AnswerScore, score_question, summarize, update_pool

# Every content field of the import format (CLAUDE.md §6), so new fields are frozen too.
SNAPSHOT_FIELDS = set().union(*(model.model_fields for model in QUESTION_MODELS)) - {"id"}

LockReason = Literal["finished", "expired", "revealed"]
AnswerStatus = Literal["correct", "partial", "wrong", "unanswered", "pending"]


class AttemptLocked(Exception):
    """The answer can no longer be changed."""

    def __init__(self, reason: LockReason) -> None:
        super().__init__(reason)
        self.reason = reason


def snapshot(question: Question) -> dict[str, Any]:
    return {name: deepcopy(getattr(question, name)) for name in SNAPSHOT_FIELDS}


def open_attempt(session: Session, exam_id: int) -> Attempt | None:
    """The unfinished attempt of an exam; there is at most one."""
    return session.exec(
        select(Attempt).where(Attempt.exam_id == exam_id, col(Attempt.finished_at).is_(None))
    ).first()


def start_exam_attempt(session: Session, exam: Exam, now: datetime | None = None) -> Attempt:
    """Exam mode: every question in file order (ignores `disabled`), feedback at the end."""
    now = now or utcnow()
    limit = exam.effective_time_limit
    attempt = Attempt(
        exam=exam,
        mode="exam",
        feedback="end",
        pass_mark=exam.effective_pass_mark,
        time_limit_minutes=limit,
        multiple_scoring=exam.multiple_scoring,
        started_at=now,
        deadline=now + timedelta(minutes=limit) if limit else None,
        answers=[
            Answer(question=question, position=position, snapshot=snapshot(question))
            for position, question in enumerate(exam.questions)
        ],
    )
    session.add(attempt)
    session.commit()
    return attempt


def restart(session: Session, attempt: Attempt) -> Attempt:
    """Discard an unfinished attempt and start a fresh one for the same exam."""
    exam = attempt.exam
    if attempt.finished_at is None:
        session.delete(attempt)
        session.commit()
    return open_attempt(session, exam.id) or start_exam_attempt(session, exam)


def score_answer(answer: Answer, multiple_scoring: MultipleScoring) -> AnswerScore:
    snap = SimpleNamespace(**answer.snapshot)
    return score_question(snap, answer.selected, answer.self_score, multiple_scoring)


def is_answered(answer: Answer) -> bool:
    return bool(answer.selected) or bool(answer.text_answer and answer.text_answer.strip())


def answer_status(answer: Answer, result: AnswerScore) -> AnswerStatus:
    if result.is_correct:
        return "correct"
    if not is_answered(answer):
        return "unanswered"
    if result.is_correct is None:
        return "pending"
    return "partial" if result.score > 0 else "wrong"


@dataclass(frozen=True)
class ReviewOption:
    text: str
    explanation: str | None
    is_correct: bool
    is_selected: bool


@dataclass(frozen=True)
class ReviewItem:
    answer: Answer
    result: AnswerScore
    status: AnswerStatus
    options: list[ReviewOption]


def review_items(attempt: Attempt) -> list[ReviewItem]:
    """Per question: the score, its status and each option marked correct/selected."""
    items = []
    for answer in attempt.answers:
        snap, selected = answer.snapshot, set(answer.selected or ())
        explanations = snap.get("option_explanations") or [None] * len(snap["options"])
        options = [
            ReviewOption(text, explanation, index in snap["correct"], index in selected)
            for index, (text, explanation) in enumerate(
                zip(snap["options"], explanations, strict=True)
            )
        ]
        result = score_answer(answer, attempt.multiple_scoring)
        items.append(ReviewItem(answer, result, answer_status(answer, result), options))
    return items


def finish(session: Session, attempt: Attempt, now: datetime | None = None) -> None:
    """Score the attempt, update the wrong pool and close it (at the deadline at the latest)."""
    if attempt.finished_at is not None:
        return
    end = now or utcnow()
    if attempt.deadline is not None:
        end = min(end, attempt.deadline)

    # Live questions (for the wrong pool) in one query instead of one per answer.
    question_ids = [a.question_id for a in attempt.answers if a.question_id is not None]
    questions = {
        q.id: q for q in session.exec(select(Question).where(col(Question.id).in_(question_ids)))
    }

    results = []
    for answer in attempt.answers:
        result = score_answer(answer, attempt.multiple_scoring)
        answer.is_correct = result.is_correct
        results.append(result)
        question = questions.get(answer.question_id)
        if question is not None and result.is_correct is not None:
            question.in_wrong_pool, question.pool_streak = update_pool(
                question.in_wrong_pool, question.pool_streak, result.is_correct
            )

    total = summarize(results, attempt.pass_mark)
    attempt.score, attempt.max_score, attempt.passed = total.score, total.max_score, total.passed
    attempt.finished_at = end
    attempt.duration_seconds = int((end - attempt.started_at).total_seconds())
    session.commit()


def finalize_expired(session: Session, now: datetime | None = None) -> None:
    """Finish attempts whose deadline passed while nobody was looking (lazy auto-submit)."""
    now = now or utcnow()
    expired = session.exec(
        select(Attempt).where(
            col(Attempt.finished_at).is_(None),
            col(Attempt.deadline).is_not(None),
            col(Attempt.deadline) <= now,
        )
    ).all()
    for attempt in expired:
        finish(session, attempt, now)


def _ensure_writable(session: Session, attempt: Attempt, now: datetime) -> None:
    if attempt.finished_at is not None:
        raise AttemptLocked("finished")
    if attempt.deadline is not None and now >= attempt.deadline:
        finish(session, attempt, now)
        raise AttemptLocked("expired")


def _check_selected(snap: dict[str, Any], selected: list[int]) -> list[int] | None:
    if any(not 0 <= index < len(snap["options"]) for index in selected):
        raise ValueError("index out of range")
    if len(set(selected)) != len(selected):
        raise ValueError("duplicate index")
    if snap["type"] == "single" and len(selected) > 1:
        raise ValueError("single choice takes one index")
    return sorted(selected) or None  # an empty selection clears the answer


def save_answer(
    session: Session,
    attempt: Attempt,
    answer: Answer,
    selected: list[int] | None = None,
    text_answer: str | None = None,
    now: datetime | None = None,
) -> None:
    """Raises AttemptLocked when the answer is frozen, ValueError when it doesn't fit the type."""
    _ensure_writable(session, attempt, now or utcnow())
    if answer.revealed:
        raise AttemptLocked("revealed")
    if answer.snapshot["type"] == "open":
        if selected is not None:
            raise ValueError("open questions take text_answer")
        answer.text_answer = text_answer or None
    else:
        if text_answer is not None:
            raise ValueError("choice questions take selected")
        answer.selected = _check_selected(answer.snapshot, selected or [])
    session.commit()
