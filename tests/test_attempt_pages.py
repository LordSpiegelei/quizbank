import json
import re

from sqlmodel import select

from app.models import Answer, Attempt, Exam
from tests.factories import db, exam, single, upload


def _setup(client, *questions, **overrides) -> int:
    upload(client, ("a.json", exam(*questions, **overrides)))
    with db(client) as session:
        return session.exec(select(Exam)).one().id


def _start(client, exam_id: int) -> int:
    response = client.post(f"/exams/{exam_id}/attempts", follow_redirects=False)
    assert response.status_code == 303
    return int(response.headers["location"].rsplit("/", 1)[1])


def _quiz_data(page: str) -> dict:
    match = re.search(r'<script type="application/json" id="quiz-data">(.*?)</script>', page, re.S)
    return json.loads(match.group(1))


def test_runner_page_hides_solutions(client) -> None:
    exam_id = _setup(
        client,
        single(question="**Fett**?", explanation="GEHEIM", option_explanations=["x", "y", "z"]),
        time_limit_minutes=10,
    )
    attempt_id = _start(client, exam_id)
    page = client.get(f"/attempts/{attempt_id}").text
    assert "GEHEIM" not in page
    quiz = _quiz_data(page)
    question = quiz["questions"][0]
    assert set(question) == {
        "id",
        "url",
        "type",
        "topic",
        "points",
        "scenario",
        "question",
        "options",
        "selected",
        "text_answer",
    }
    assert question["question"] == "<p><strong>Fett</strong>?</p>\n"
    assert 590 < quiz["remaining"] <= 600


def test_start_resumes_and_restart_discards(client) -> None:
    exam_id = _setup(client)
    attempt_id = _start(client, exam_id)
    assert _start(client, exam_id) == attempt_id  # at most one unfinished attempt
    assert "Fortsetzen" in client.get(f"/exams/{exam_id}").text
    url = _quiz_data(client.get(f"/attempts/{attempt_id}").text)["questions"][0]["url"]
    client.put(url, json={"selected": [1]})

    response = client.post(f"/attempts/{attempt_id}/restart", follow_redirects=False)
    new_id = int(response.headers["location"].rsplit("/", 1)[1])
    with db(client) as session:
        assert [a.id for a in session.exec(select(Attempt))] == [new_id]
        assert session.exec(select(Answer)).one().selected is None  # fresh attempt


def test_save_finish_review_and_history(client) -> None:
    exam_id = _setup(
        client,
        single(explanation="Weil B.", source="Folie 3", option_explanations=["a", "b", "c"]),
        single(id="q2", correct=[0]),
        pass_mark=1,
    )
    attempt_id = _start(client, exam_id)
    answers = _quiz_data(client.get(f"/attempts/{attempt_id}").text)["questions"]

    assert client.put(answers[0]["url"], json={"selected": [1]}).status_code == 204
    assert client.put(answers[0]["url"], json={"selected": [7]}).status_code == 422
    assert client.put(answers[0]["url"], json={"foo": 1}).status_code == 422
    other = answers[0]["url"].replace(f"/attempts/{attempt_id}/", "/attempts/999/")
    assert client.put(other, json={"selected": [1]}).status_code == 404

    response = client.post(f"/attempts/{attempt_id}/finish", follow_redirects=False)
    assert response.headers["location"] == f"/attempts/{attempt_id}/review"
    assert client.put(answers[1]["url"], json={"selected": [0]}).status_code == 409

    review = client.get(f"/attempts/{attempt_id}/review").text
    assert "Weil B." in review
    assert "Folie 3" in review
    assert "unbeantwortet" in review
    assert "bestanden" in review

    # Finished attempts open the review; the exam page lists them.
    response = client.get(f"/attempts/{attempt_id}", follow_redirects=False)
    assert response.headers["location"] == f"/attempts/{attempt_id}/review"
    exam_page = client.get(f"/exams/{exam_id}").text
    assert f"/attempts/{attempt_id}/review" in exam_page
    assert "Klausur starten" in exam_page

    response = client.post(f"/attempts/{attempt_id}/delete", follow_redirects=False)
    assert response.headers["location"] == f"/exams/{exam_id}"
    with db(client) as session:
        assert session.exec(select(Answer)).all() == []


def test_unfinished_review_redirects_to_runner(client) -> None:
    attempt_id = _start(client, _setup(client))
    response = client.get(f"/attempts/{attempt_id}/review", follow_redirects=False)
    assert response.headers["location"] == f"/attempts/{attempt_id}"
    assert client.get("/attempts/999").status_code == 404
