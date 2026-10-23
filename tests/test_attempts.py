from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import select

from app.attempts import (
    AttemptLocked,
    answer_status,
    finalize_expired,
    finish,
    open_attempt,
    save_answer,
    score_answer,
    start_exam_attempt,
)
from app.models import Attempt, Exam, Question
from tests.factories import import_file, open_q, single

T0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)


def _exam(session, *questions, **overrides) -> Exam:
    return session.get(Exam, import_file(session, *questions, **overrides).exam_id)


def _two_singles(session, **overrides) -> Exam:
    return _exam(session, single(), single(id="q2", correct=[0]), **overrides)


def test_start_freezes_questions_and_settings(session) -> None:
    exam_row = _two_singles(session, time_limit_minutes=30, pass_mark=1)
    session.exec(select(Question).where(Question.external_id == "q2")).one().disabled = True
    attempt = start_exam_attempt(session, exam_row, now=T0)

    assert (attempt.mode, attempt.feedback) == ("exam", "end")
    assert (attempt.pass_mark, attempt.time_limit_minutes) == (1, 30)
    assert attempt.deadline == T0 + timedelta(minutes=30)
    # Exam mode ignores `disabled`: every question, in file order.
    assert [a.position for a in attempt.answers] == [0, 1]
    assert [a.snapshot["correct"] for a in attempt.answers] == [[1], [0]]
    assert attempt.answers[0].snapshot["options"] == ["A", "B", "C"]
    assert all(a.selected is None for a in attempt.answers)
    assert open_attempt(session, exam_row.id) == attempt


def test_start_uses_course_defaults_and_no_deadline_without_limit(session) -> None:
    exam_row = _two_singles(session)
    exam_row.course.default_pass_mark = 2
    attempt = start_exam_attempt(session, exam_row, now=T0)
    assert attempt.pass_mark == 2
    assert attempt.deadline is None


def test_reimport_leaves_snapshot_alone(session) -> None:
    exam_row = _two_singles(session)
    attempt = start_exam_attempt(session, exam_row, now=T0)
    import_file(session, single(options=["X", "Y"], correct=[0]))
    session.refresh(attempt)
    assert attempt.answers[0].snapshot["options"] == ["A", "B", "C"]
    assert attempt.answers[1].question_id is None  # q2 left the file
    assert attempt.answers[1].snapshot["correct"] == [0]


def test_save_answer_validates(session) -> None:
    exam_row = _exam(session, single(), single(id="m", type="multiple", correct=[0, 2]), open_q())
    attempt = start_exam_attempt(session, exam_row, now=T0)
    single_a, multiple_a, open_a = attempt.answers

    save_answer(session, attempt, multiple_a, selected=[2, 0], now=T0)
    assert multiple_a.selected == [0, 2]
    save_answer(session, attempt, open_a, text_answer="Text", now=T0)
    assert open_a.text_answer == "Text"
    save_answer(session, attempt, single_a, selected=[1], now=T0)
    save_answer(session, attempt, single_a, selected=[], now=T0)
    assert single_a.selected is None  # cleared

    for answer, kwargs in [
        (single_a, {"selected": [3]}),
        (single_a, {"selected": [0, 1]}),
        (multiple_a, {"selected": [1, 1]}),
        (single_a, {"text_answer": "x"}),
        (open_a, {"selected": [0]}),
    ]:
        with pytest.raises(ValueError):
            save_answer(session, attempt, answer, now=T0, **kwargs)


def test_save_answer_locked(session) -> None:
    exam_row = _two_singles(session, time_limit_minutes=10)
    attempt = start_exam_attempt(session, exam_row, now=T0)
    first, second = attempt.answers

    second.revealed = True
    with pytest.raises(AttemptLocked) as exc:
        save_answer(session, attempt, second, selected=[0], now=T0)
    assert exc.value.reason == "revealed"

    with pytest.raises(AttemptLocked) as exc:
        save_answer(session, attempt, first, selected=[1], now=T0 + timedelta(minutes=10))
    assert exc.value.reason == "expired"
    assert attempt.finished_at == T0 + timedelta(minutes=10)  # finished on the way

    with pytest.raises(AttemptLocked) as exc:
        save_answer(session, attempt, first, selected=[1], now=T0)
    assert exc.value.reason == "finished"


def test_finish_scores_and_passes(session) -> None:
    exam_row = _two_singles(session, pass_mark=1)
    attempt = start_exam_attempt(session, exam_row, now=T0)
    save_answer(session, attempt, attempt.answers[0], selected=[1], now=T0)
    finish(session, attempt, now=T0 + timedelta(seconds=95))

    assert (attempt.score, attempt.max_score, attempt.passed) == (1, 2, True)
    assert attempt.duration_seconds == 95
    assert [a.is_correct for a in attempt.answers] == [True, False]  # unanswered counts as wrong
    assert open_attempt(session, exam_row.id) is None

    finish(session, attempt, now=T0 + timedelta(hours=1))  # finishing twice is a no-op
    assert attempt.duration_seconds == 95


def test_finish_leaves_passed_open_until_self_assessed(session) -> None:
    exam_row = _exam(session, single(), open_q(), pass_mark=1)
    attempt = start_exam_attempt(session, exam_row, now=T0)
    save_answer(session, attempt, attempt.answers[0], selected=[1], now=T0)
    finish(session, attempt, now=T0)
    assert attempt.passed is None
    assert attempt.answers[1].is_correct is None


def test_finish_updates_wrong_pool(session) -> None:
    exam_row = _two_singles(session)
    q1, q2 = sorted(exam_row.questions, key=lambda q: q.position)

    attempt = start_exam_attempt(session, exam_row, now=T0)
    save_answer(session, attempt, attempt.answers[0], selected=[0], now=T0)  # wrong
    save_answer(session, attempt, attempt.answers[1], selected=[0], now=T0)  # right
    finish(session, attempt, now=T0)
    assert (q1.in_wrong_pool, q1.pool_streak) == (True, 0)
    assert q2.in_wrong_pool is False

    for streak, in_pool in [(1, True), (0, False)]:
        attempt = start_exam_attempt(session, exam_row, now=T0)
        save_answer(session, attempt, attempt.answers[0], selected=[1], now=T0)
        finish(session, attempt, now=T0)
        assert (q1.in_wrong_pool, q1.pool_streak) == (in_pool, streak)


def test_finalize_expired_caps_at_deadline(session) -> None:
    exam_row = _two_singles(session, time_limit_minutes=5)
    attempt = start_exam_attempt(session, exam_row, now=T0)
    finalize_expired(session, now=T0 + timedelta(minutes=4))
    assert attempt.finished_at is None

    finalize_expired(session, now=T0 + timedelta(hours=3))
    assert attempt.finished_at == T0 + timedelta(minutes=5)
    assert attempt.duration_seconds == 300
    assert attempt.score == 0


def test_answer_status(session) -> None:
    exam_row = _exam(
        session,
        single(),
        single(id="m", type="multiple", correct=[0, 2]),
        single(id="w"),
        single(id="u"),
        open_q(),
        multiple_scoring="partial",
    )
    attempt = start_exam_attempt(session, exam_row, now=T0)
    for answer, selected in zip(attempt.answers, [[1], [0], [0], None], strict=False):
        if selected:
            save_answer(session, attempt, answer, selected=selected, now=T0)
    save_answer(session, attempt, attempt.answers[4], text_answer="…", now=T0)
    finish(session, attempt, now=T0)

    statuses = [
        answer_status(a, score_answer(a, attempt.multiple_scoring)) for a in attempt.answers
    ]
    assert statuses == ["correct", "partial", "wrong", "unanswered", "pending"]


def test_deleting_attempt_keeps_questions(session) -> None:
    exam_row = _two_singles(session)
    attempt = start_exam_attempt(session, exam_row, now=T0)
    session.delete(attempt)
    session.commit()
    assert session.exec(select(Attempt)).all() == []
    assert len(session.exec(select(Question)).all()) == 2
