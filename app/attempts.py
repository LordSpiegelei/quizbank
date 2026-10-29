"""Attempt lifecycle: start with frozen question snapshots, save answers, finish and score."""

import random
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, PositiveInt, model_validator
from sqlmodel import Session, col, select

from app.models import Answer, Attempt, Exam, Question, utcnow
from app.queries import live_questions
from app.schemas import QUESTION_FIELDS, MultipleScoring
from app.scoring import AnswerScore, score_question, summarize, update_pool

# Every content field of the import format (CLAUDE.md §6), so new fields are frozen too.
SNAPSHOT_FIELDS = set().union(*QUESTION_FIELDS.values())

Mode = Literal["exam", "practice", "retry_wrong"]
Feedback = Literal["instant", "end"]
LockReason = Literal["finished", "expired", "revealed"]
AnswerStatus = Literal["correct", "partial", "wrong", "unanswered", "pending"]


class AttemptLocked(Exception):
    """The answer can no longer be changed."""

    def __init__(self, reason: LockReason) -> None:
        super().__init__(reason)
        self.reason = reason


class NoQuestions(Exception):
    """The filters left no question to ask."""


def _blank_to_none(value: Any) -> Any:
    return None if value == "" else value  # an empty form field means "no limit"


# Settings a mode overrides because they don't apply to it.
_NO_FILTERS = {"topics": [], "tags": [], "starred_only": False}
_MODE_OVERRIDES: dict[str, dict[str, Any]] = {
    "exam": _NO_FILTERS | {"feedback": "end", "count": None},  # asks everything
    "retry_wrong": _NO_FILTERS,  # the pool decides which questions come up
}


class StartOptions(BaseModel):
    """What the start dialog chose (it is the form model too); kept in Attempt.filter so a
    restart can repeat it."""

    mode: Mode = "exam"
    feedback: Feedback = "end"
    topics: list[str] = []
    tags: list[str] = []
    starred_only: bool = False
    count: Annotated[PositiveInt | None, BeforeValidator(_blank_to_none)] = None
    shuffle_questions: bool = False
    shuffle_options: bool = False

    @model_validator(mode="before")
    @classmethod
    def _apply_mode_overrides(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        return data | _MODE_OVERRIDES.get(data.get("mode", "exam"), {})

    @classmethod
    def of(cls, attempt: Attempt) -> "StartOptions":
        stored = attempt.filter or {}
        return cls.model_validate(stored | {"mode": attempt.mode, "feedback": attempt.feedback})

    def as_filter(self) -> dict[str, Any]:
        return self.model_dump(exclude={"mode", "feedback"})


EXAM = StartOptions()


def snapshot(question: Question) -> dict[str, Any]:
    return {name: deepcopy(getattr(question, name)) for name in SNAPSHOT_FIELDS}


def askable(question: Question, mode: Mode) -> bool:
    """Exam mode asks everything; practice skips disabled questions; retry_wrong also needs
    the question to be in the wrong pool."""
    if mode == "exam":
        return True
    return not question.disabled and (mode != "retry_wrong" or question.in_wrong_pool)


def pick_questions(
    questions: Sequence[Question], options: StartOptions, rng: random.Random
) -> list[Question]:
    """Topics and tags each match any of the chosen values; `count` draws a random sample."""
    topics, tags = set(options.topics), set(options.tags)
    picked = [
        q
        for q in questions
        if askable(q, options.mode)
        and (not options.starred_only or q.starred)
        and (not topics or q.topic in topics)
        and (not tags or tags.intersection(q.tags))
    ]
    if options.count is not None and options.count < len(picked):
        picked = sorted(rng.sample(picked, options.count), key=lambda q: q.position)
    if options.shuffle_questions:
        rng.shuffle(picked)
    return picked


def shuffle_options(snap: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """Options in random order, with `correct` and `option_explanations` mapped along."""
    if not snap["options"]:
        return snap
    order = rng.sample(range(len(snap["options"])), len(snap["options"]))
    shuffled = snap | {
        "options": [snap["options"][i] for i in order],
        "correct": sorted(order.index(i) for i in snap["correct"]),
    }
    if snap["option_explanations"]:
        shuffled["option_explanations"] = [snap["option_explanations"][i] for i in order]
    return shuffled


def open_attempt(session: Session, exam_id: int) -> Attempt | None:
    """The unfinished attempt of an exam; there is at most one."""
    return session.exec(
        select(Attempt).where(Attempt.exam_id == exam_id, col(Attempt.finished_at).is_(None))
    ).first()


def start_attempt(
    session: Session,
    exam: Exam,
    options: StartOptions = EXAM,
    now: datetime | None = None,
    rng: random.Random | None = None,
) -> Attempt:
    """Exam mode has the countdown and the pass mark, practice neither.
    Raises NoQuestions when the filters match nothing."""
    now, rng = now or utcnow(), rng or random.Random()
    questions = pick_questions(exam.questions, options, rng)
    if not questions:
        raise NoQuestions
    # Read the exam settings first: the lazy course load would autoflush half-built answers.
    is_exam = options.mode == "exam"
    limit = exam.effective_time_limit if is_exam else None
    pass_mark = exam.effective_pass_mark if is_exam else None
    answers = []
    for position, question in enumerate(questions):
        snap = snapshot(question)
        if options.shuffle_options:
            snap = shuffle_options(snap, rng)
        answers.append(Answer(question=question, position=position, snapshot=snap))
    attempt = Attempt(
        exam=exam,
        mode=options.mode,
        feedback=options.feedback,
        filter=options.as_filter(),
        pass_mark=pass_mark,
        time_limit_minutes=limit,
        multiple_scoring=exam.multiple_scoring,
        started_at=now,
        deadline=now + timedelta(minutes=limit) if limit else None,
        answers=answers,
    )
    session.add(attempt)
    session.commit()
    return attempt


def restart(session: Session, attempt: Attempt) -> Attempt:
    """Discard an unfinished attempt and start a fresh one with the same settings."""
    exam, options = attempt.exam, StartOptions.of(attempt)
    if attempt.finished_at is None:
        session.delete(attempt)
        session.commit()
    return open_attempt(session, exam.id) or start_attempt(session, exam, options)


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


def review_item(answer: Answer, multiple_scoring: MultipleScoring) -> ReviewItem:
    """The score, its status and each option marked correct/selected; used by the review
    page and by instant feedback."""
    snap, selected = answer.snapshot, set(answer.selected or ())
    explanations = snap.get("option_explanations") or [None] * len(snap["options"])
    options = [
        ReviewOption(text, explanation, index in snap["correct"], index in selected)
        for index, (text, explanation) in enumerate(zip(snap["options"], explanations, strict=True))
    ]
    result = score_answer(answer, multiple_scoring)
    return ReviewItem(answer, result, answer_status(answer, result), options)


def review_items(attempt: Attempt) -> list[ReviewItem]:
    return [review_item(answer, attempt.multiple_scoring) for answer in attempt.answers]


def finish(session: Session, attempt: Attempt, now: datetime | None = None) -> None:
    """Score the attempt, update the wrong pool and close it (at the deadline at the latest)."""
    if attempt.finished_at is not None:
        return
    end = now or utcnow()
    if attempt.deadline is not None:
        end = min(end, attempt.deadline)

    questions = live_questions(session, attempt)  # for the wrong pool

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


def reveal(session: Session, attempt: Attempt, answer: Answer, now: datetime | None = None) -> None:
    """Instant feedback: show the solution; the answer is locked from then on."""
    _ensure_writable(session, attempt, now or utcnow())
    if attempt.feedback != "instant":
        raise ValueError("this attempt gives feedback at the end")
    if not answer.revealed:
        answer.revealed = True
        session.commit()
