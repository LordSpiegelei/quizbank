import pytest
from sqlmodel import select

from app.attempts import start_attempt
from app.llm import LLMError, LLMReplyError
from app.models import Answer, Attempt, Exam
from app.review import ai_review, parse_review
from tests.factories import FakeLLM, db, exam, open_q, single, upload
from tests.test_attempts import _exam

RUBRIC = [{"criterion": "Nennt Risiken", "points": 4}, {"criterion": "Empfiehlt", "points": 6}]


def _finished_attempt(client, **open_fields) -> tuple[int, int]:
    """A finished attempt with one open question; returns (attempt_id, answer_id)."""
    upload(client, ("a.json", exam(single(), open_q(points=10, **open_fields))))
    with db(client) as session:
        exam_id = session.exec(select(Exam)).one().id
    response = client.post(f"/exams/{exam_id}/attempts", follow_redirects=False)
    attempt_id = int(response.headers["location"].rsplit("/", 1)[1])
    with db(client) as session:
        answer = session.exec(select(Answer).where(Answer.position == 1)).one()
        answer_id = answer.id
    client.put(
        f"/api/attempts/{attempt_id}/answers/{answer_id}", json={"text_answer": "Meine Analyse"}
    )
    client.post(f"/attempts/{attempt_id}/finish")
    return attempt_id, answer_id


def _url(attempt_id: int, answer_id: int) -> str:
    return f"/api/attempts/{attempt_id}/answers/{answer_id}/ai-review"


def test_parse_review_clamps_scores() -> None:
    assert parse_review({"scores": [9, -1], "feedback": " ok "}, [4, 6]) == ([4, 0], "ok")


@pytest.mark.parametrize(
    "reply",
    [
        [],
        {"scores": [1], "feedback": "x"},  # wrong count
        {"scores": ["1", 2], "feedback": "x"},
        {"scores": [True, 2], "feedback": "x"},
        {"scores": [1, 2], "feedback": " "},
    ],
)
def test_parse_review_rejects(reply) -> None:
    with pytest.raises(ValueError):
        parse_review(reply, [4, 6])


def test_ai_review_stores_suggestion_not_score(client) -> None:
    attempt_id, answer_id = _finished_attempt(client, rubric=RUBRIC)
    client.app.state.llm = llm = FakeLLM({"scores": [3, 5.5], "feedback": "**Gut**"})

    response = client.post(_url(attempt_id, answer_id), json={"model": "other"})

    assert response.status_code == 200
    data = response.json()
    assert data["scores"] == [3, 5.5] and data["model"] == "other"
    assert "<strong>Gut</strong>" in data["feedback_html"]
    assert llm.calls[0]["model"] == "other"
    assert "Meine Analyse" in llm.calls[0]["messages"][0]["content"]
    assert "German" in llm.calls[0]["messages"][0]["content"]  # tests run with APP_LANG=de
    with db(client) as session:
        answer = session.get(Answer, answer_id)
        assert (answer.ai_scores, answer.ai_feedback, answer.ai_model) == (
            [3, 5.5],
            "**Gut**",
            "other",
        )
        assert answer.ai_reviewed_at is not None
        assert answer.self_score is None and answer.rubric_scores is None
        attempt = session.get(Attempt, attempt_id)
        assert attempt.passed is None  # still pending: only the user's assessment counts


def test_ai_review_without_rubric_uses_one_score(client) -> None:
    attempt_id, answer_id = _finished_attempt(client)
    client.app.state.llm = llm = FakeLLM({"scores": [12], "feedback": "viel"})
    response = client.post(_url(attempt_id, answer_id), json={})
    assert response.json()["scores"] == [10]  # clamped to the question's points
    assert llm.calls[0]["schema"]["properties"]["scores"]["minItems"] == 1
    assert llm.calls[0]["model"] == "fake"  # the default model


def test_ai_review_retries_once_then_succeeds(client) -> None:
    attempt_id, answer_id = _finished_attempt(client, rubric=RUBRIC)
    client.app.state.llm = llm = FakeLLM(
        LLMReplyError("did not return valid JSON"), {"scores": [1, 1], "feedback": "ok"}
    )
    assert client.post(_url(attempt_id, answer_id), json={}).status_code == 200
    assert len(llm.calls) == 2
    assert "unusable" in llm.calls[1]["messages"][-1]["content"]


def test_ai_review_gives_up_after_retry_and_stores_nothing(client) -> None:
    attempt_id, answer_id = _finished_attempt(client, rubric=RUBRIC)
    client.app.state.llm = llm = FakeLLM(
        {"scores": [1], "feedback": "x"}, {"scores": [], "feedback": "x"}
    )
    response = client.post(_url(attempt_id, answer_id), json={})
    assert response.status_code == 502
    assert len(llm.calls) == 2
    with db(client) as session:
        assert session.get(Answer, answer_id).ai_feedback is None


def test_ai_review_unreachable_is_502_without_retry(client) -> None:
    attempt_id, answer_id = _finished_attempt(client)
    client.app.state.llm = llm = FakeLLM(LLMError("cannot reach the LLM"))
    response = client.post(_url(attempt_id, answer_id), json={})
    assert response.status_code == 502 and "cannot reach" in response.json()["detail"]
    assert len(llm.calls) == 1


def test_ai_review_needs_finished_attempt_and_open_question(client, session) -> None:
    upload(client, ("a.json", exam(single(), open_q())))
    with db(client) as s:
        exam_id = s.exec(select(Exam)).one().id
    response = client.post(f"/exams/{exam_id}/attempts", follow_redirects=False)
    attempt_id = int(response.headers["location"].rsplit("/", 1)[1])
    client.app.state.llm = FakeLLM()
    with db(client) as s:
        open_answer = s.exec(select(Answer).where(Answer.position == 1)).one()
        choice_answer = s.exec(select(Answer).where(Answer.position == 0)).one()
        assert client.post(_url(attempt_id, open_answer.id), json={}).status_code == 409
        client.post(f"/attempts/{attempt_id}/finish")
        assert client.post(_url(attempt_id, choice_answer.id), json={}).status_code == 422


def test_ai_review_disabled_is_404(client) -> None:
    attempt_id, answer_id = _finished_attempt(client)
    assert client.app.state.llm is None
    assert client.post(_url(attempt_id, answer_id), json={}).status_code == 404


def test_ai_review_function_rejects_unfinished(session) -> None:
    attempt = start_attempt(session, _exam(session, open_q()))
    with pytest.raises(ValueError):
        ai_review(session, FakeLLM(), "m", attempt, attempt.answers[0], "en")


def test_review_page_without_llm_has_no_ai_section(client) -> None:
    attempt_id, _ = _finished_attempt(client)
    assert "data-ai-review" not in client.get(f"/attempts/{attempt_id}").text


def test_review_page_offers_models_and_shows_stored_feedback(client) -> None:
    attempt_id, answer_id = _finished_attempt(client, rubric=RUBRIC)
    client.app.state.llm = FakeLLM({"scores": [2, 3], "feedback": "Fehlt: Empfehlung"})
    page = client.get(f"/attempts/{attempt_id}").text
    assert "data-ai-review" in page and '<option value="other"' in page
    assert "Fehlt: Empfehlung" not in page

    client.post(_url(attempt_id, answer_id), json={})
    page = client.get(f"/attempts/{attempt_id}").text
    assert "Fehlt: Empfehlung" in page and "data-ai-apply" in page


def test_review_page_when_llm_unreachable(client) -> None:
    attempt_id, _ = _finished_attempt(client)
    client.app.state.llm = FakeLLM(models=LLMError("down"))
    response = client.get(f"/attempts/{attempt_id}")
    assert response.status_code == 200
    assert "data-ai-run" not in response.text and "data-ai-review" in response.text
