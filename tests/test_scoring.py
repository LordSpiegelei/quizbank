from types import SimpleNamespace

import pytest

from app.scoring import (
    AnswerScore,
    ExamScore,
    score_open,
    score_question,
    self_score_from,
    summarize,
    update_pool,
)


def question(type: str = "single", correct=(1,), points: float = 1) -> SimpleNamespace:
    return SimpleNamespace(type=type, correct=list(correct), points=points)


@pytest.mark.parametrize("mode", ["all_or_nothing", "partial"])
@pytest.mark.parametrize(
    ("selected", "score", "is_correct"),
    [([1], 1, True), ([0], 0, False), ([], 0, False), (None, 0, False), ([0, 1], 0, False)],
)
def test_single(selected, score, is_correct, mode) -> None:
    result = score_question(question(), selected, multiple_scoring=mode)
    assert result == AnswerScore(score, 1, is_correct)


@pytest.mark.parametrize(
    ("selected", "expected"),
    [
        ([0, 2], AnswerScore(2, 2, True)),
        ([2, 0], AnswerScore(2, 2, True)),  # order does not matter
        ([0], AnswerScore(0, 2, False)),
        ([0, 2, 3], AnswerScore(0, 2, False)),
        (None, AnswerScore(0, 2, False)),
    ],
)
def test_multiple_all_or_nothing(selected, expected) -> None:
    assert score_question(question("multiple", (0, 2), points=2), selected) == expected


@pytest.mark.parametrize(
    ("correct", "points", "selected", "score"),
    [
        ((0, 2), 2, [0, 2], 2),  # all correct
        ((0, 2), 2, [0], 1),  # one of two correct picks
        ((0, 2), 2, [0, 3], 0),  # wrong pick cancels the correct one
        ((0, 2), 2, [0, 2, 3], 1),
        ((0, 2), 2, [1, 3], 0),  # never negative
        ((0, 2), 2, [], 0),
        ((0, 1, 2), 3, [0, 1], 2),
        ((0, 1, 2), 1, [0], pytest.approx(1 / 3)),
    ],
)
def test_multiple_partial(correct, points, selected, score) -> None:
    result = score_question(
        question("multiple", correct, points), selected, multiple_scoring="partial"
    )
    assert result.score == score
    assert result.max_score == points
    assert result.is_correct is (set(selected) == set(correct))


@pytest.mark.parametrize(
    ("self_score", "expected"),
    [
        (None, AnswerScore(0, 10, None)),
        (0, AnswerScore(0, 10, False)),
        (6, AnswerScore(6, 10, False)),
        (10, AnswerScore(10, 10, True)),
        (12, AnswerScore(10, 10, True)),  # capped at the question's points
        (-1, AnswerScore(0, 10, False)),
    ],
)
def test_open(self_score, expected) -> None:
    assert score_open(10, self_score) == expected
    assert score_question(question("open", (), points=10), self_score=self_score) == expected


@pytest.mark.parametrize(
    ("pass_mark", "passed"),
    [(30, True), (30.5, False), (None, None), (0, True)],
)
def test_summarize_pass_mark(pass_mark, passed) -> None:
    results = [AnswerScore(1, 1, True)] * 30 + [AnswerScore(0, 1, False)] * 30
    assert summarize(results, pass_mark) == ExamScore(30, 60, passed)


def test_summarize_pending_open_question() -> None:
    results = [AnswerScore(5, 5, True), AnswerScore(0, 10, None)]
    assert summarize(results, pass_mark=3) == ExamScore(5, 15, None)


def test_summarize_rounds_float_noise() -> None:
    result = summarize([AnswerScore(0.1, 1, False), AnswerScore(0.2, 1, False)], pass_mark=0.3)
    assert result.score == 0.3
    assert result.passed is True


def test_summarize_empty() -> None:
    assert summarize([], pass_mark=1) == ExamScore(0, 0, False)


@pytest.mark.parametrize(
    ("in_pool", "streak", "is_correct", "expected"),
    [
        (False, 0, False, (True, 0)),  # wrong: into the pool
        (True, 1, False, (True, 0)),  # wrong again: streak resets
        (True, 0, True, (True, 1)),  # first correct answer: stays in the pool
        (True, 1, True, (False, 0)),  # second in a row: out
        (False, 0, True, (False, 0)),  # correct outside the pool: nothing changes
    ],
)
def test_update_pool(in_pool, streak, is_correct, expected) -> None:
    assert update_pool(in_pool, streak, is_correct) == expected


RUBRIC = [{"criterion": "a", "points": 4}, {"criterion": "b", "points": 6}]


@pytest.mark.parametrize(
    ("rubric", "scores", "total"),
    [(RUBRIC, [2, 5.5], 7.5), (RUBRIC, [4, 6], 10), (None, [3], 3), (None, [0], 0)],
)
def test_self_score_from(rubric, scores, total) -> None:
    assert self_score_from(10, rubric, scores) == total


@pytest.mark.parametrize(
    ("rubric", "scores"),
    [(RUBRIC, [5, 0]), (RUBRIC, [1]), (RUBRIC, [-1, 2]), (None, [11]), (None, []), (None, [1, 2])],
)
def test_self_score_from_rejects(rubric, scores) -> None:
    with pytest.raises(ValueError):
        self_score_from(10, rubric, scores)
