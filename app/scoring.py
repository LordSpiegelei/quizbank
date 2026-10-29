"""Scoring rules (CLAUDE.md §10) as pure functions, independent of the database."""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from app.schemas import MultipleScoring


@dataclass(frozen=True)
class AnswerScore:
    score: float
    max_score: float
    is_correct: bool | None  # None: open question not self-assessed yet


@dataclass(frozen=True)
class ExamScore:
    score: float
    max_score: float
    passed: bool | None  # None: no pass mark set, or open questions still unassessed


class ScorableQuestion(Protocol):
    """The fields scoring needs; app.models.Question satisfies it (tests use plain objects)."""

    type: str
    correct: list[int]
    points: float


def score_choice(
    correct: Sequence[int],
    selected: Sequence[int] | None,
    points: float,
    multiple_scoring: MultipleScoring = "all_or_nothing",
) -> AnswerScore:
    """single/multiple: full points for the exact set of correct options.

    With `partial`, each correct pick earns points / len(correct) and each wrong pick cancels
    one correct pick; the score never drops below 0. With a single correct option (always the
    case for `single`) this is the same as all-or-nothing.
    """
    right, chosen = set(correct), set(selected or ())
    if chosen == right:
        return AnswerScore(points, points, True)
    if multiple_scoring == "partial" and right:
        hits = len(chosen & right) - len(chosen - right)
        return AnswerScore(points * max(0, hits) / len(right), points, False)
    return AnswerScore(0, points, False)


def score_open(points: float, self_score: float | None) -> AnswerScore:
    """open: the self-assessed rubric total, clamped to 0..points."""
    if self_score is None:
        return AnswerScore(0, points, None)
    score = min(max(self_score, 0), points)
    return AnswerScore(score, points, score >= points)


def self_score_from(
    points: float, rubric: Sequence[Mapping[str, Any]] | None, scores: Sequence[float]
) -> float:
    """The self-assessed total of an open question: one score per rubric criterion (each
    0..its points), or a single 0..points score without a rubric. Raises ValueError."""
    limits = [item["points"] for item in rubric] if rubric else [points]
    if len(scores) != len(limits):
        raise ValueError("one score per rubric criterion")
    if any(not 0 <= score <= limit for score, limit in zip(scores, limits, strict=True)):
        raise ValueError("score out of range")
    return sum(scores)


def score_question(
    question: ScorableQuestion,
    selected: Sequence[int] | None = None,
    self_score: float | None = None,
    multiple_scoring: MultipleScoring = "all_or_nothing",
) -> AnswerScore:
    """Score one question; call it for unanswered questions too so they count toward the max."""
    if question.type == "open":
        return score_open(question.points, self_score)
    return score_choice(question.correct, selected, question.points, multiple_scoring)


POOL_EXIT_STREAK = 2  # correct answers in a row that take a question out of the wrong pool
POOL_OUT = (False, 0)  # (in_pool, streak) of a question that is not in the wrong pool


def update_pool(in_pool: bool, streak: int, is_correct: bool) -> tuple[bool, int]:
    """Wrong-pool state after one answer: wrong (or unanswered) puts the question in the pool
    and resets its streak; correct answers count up until POOL_EXIT_STREAK removes it."""
    if not is_correct:
        return True, 0
    if not in_pool or streak + 1 >= POOL_EXIT_STREAK:
        return POOL_OUT
    return True, streak + 1


def summarize(results: Iterable[AnswerScore], pass_mark: float | None) -> ExamScore:
    """Totals of an attempt; passed means score >= pass_mark."""
    score = max_score = 0.0
    pending = False
    for result in results:
        score += result.score
        max_score += result.max_score
        pending = pending or result.is_correct is None
    # Round away float noise (0.1 + 0.2) so `>= pass_mark` behaves as expected.
    score, max_score = round(score, 6), round(max_score, 6)
    passed = None if pass_mark is None or pending else score >= pass_mark
    return ExamScore(score, max_score, passed)
