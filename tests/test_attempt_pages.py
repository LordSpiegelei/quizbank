import json
import re

from sqlmodel import select

from app.models import Answer, Attempt, Exam
from tests.factories import db, exam, single, upload


def _setup(client, *questions, **overrides) -> int:
    upload(client, ("a.json", exam(*questions, **overrides)))
    with db(client) as session:
        return session.exec(select(Exam)).one().id


def _start(client, exam_id: int, **form) -> int:
    response = client.post(f"/exams/{exam_id}/attempts", data=form, follow_redirects=False)
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
        "flags",
        "flagUrls",
        "id",
        "url",
        "revealUrl",
        "solution",
        "type",
        "topic",
        "points",
        "scenario",
        "question",
        "options",
        "selected",
        "text_answer",
    }
    assert question["solution"] is None
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


def test_practice_with_instant_feedback(client) -> None:
    exam_id = _setup(
        client,
        single(topic="Algebra", explanation="Weil B."),
        single(id="q2", topic="Geometrie"),
        time_limit_minutes=10,
    )
    attempt_id = _start(client, exam_id, mode="practice", feedback="instant", topics=["Algebra"])
    page = client.get(f"/attempts/{attempt_id}").text
    quiz = _quiz_data(page)
    assert quiz["feedback"] == "instant"
    assert quiz["remaining"] is None  # practice has no countdown
    assert len(quiz["questions"]) == 1
    question = quiz["questions"][0]
    assert question["solution"] is None
    assert "Weil B." not in page

    client.put(question["url"], json={"selected": [1]})
    solution = client.post(question["revealUrl"]).json()
    assert solution["status"] == "correct"
    assert solution["correct"] == [1]
    assert "Weil B." in solution["explanation"]
    assert client.put(question["url"], json={"selected": [0]}).status_code == 409

    # Resuming shows the revealed solution again.
    resumed = _quiz_data(client.get(f"/attempts/{attempt_id}").text)["questions"][0]
    assert resumed["solution"]["status"] == "correct"


def test_reveal_rejected_in_exam_mode(client) -> None:
    attempt_id = _start(client, _setup(client))
    question = _quiz_data(client.get(f"/attempts/{attempt_id}").text)["questions"][0]
    assert client.post(question["revealUrl"]).status_code == 422


def test_start_without_matching_questions(client) -> None:
    exam_id = _setup(client, single(tags=["x"]))
    response = client.post(f"/exams/{exam_id}/attempts", data={"mode": "practice", "tags": ["y"]})
    assert response.status_code == 422
    assert "Keine Frage passt" in response.text
    with db(client) as session:
        assert session.exec(select(Attempt)).all() == []


def test_exam_page_offers_filters(client) -> None:
    exam_id = _setup(
        client, single(topic="A", tags=["t1"]), single(id="q2", topic="B", tags=["t2"])
    )
    page = client.get(f"/exams/{exam_id}").text
    assert "Übung starten" in page
    assert 'name="topics" value="A"' in page
    assert 'name="tags" value="t2"' in page


def test_start_form_fields(client) -> None:
    exam_id = _setup(client, single(), single(id="q2", tags=["t"]), single(id="q3", tags=["t"]))
    form = {"mode": "practice", "count": "", "tags": ["t", "x"], "shuffle_options": "true"}
    attempt_id = _start(client, exam_id, **form)
    with db(client) as session:
        attempt = session.get(Attempt, attempt_id)
        assert attempt.filter == {
            "topics": [],
            "tags": ["t", "x"],
            "starred_only": False,
            "count": None,  # an empty field means all
            "shuffle_questions": False,
            "shuffle_options": True,
        }
        assert len(attempt.answers) == 2
    client.post(f"/attempts/{attempt_id}/restart")

    with db(client) as session:
        session.delete(session.exec(select(Attempt)).one())
        session.commit()
    response = client.post(f"/exams/{exam_id}/attempts", data={"mode": "practice", "count": "0"})
    assert response.status_code == 422


def test_wrong_pool_retry_and_removal(client) -> None:
    exam_id = _setup(client, single(question="Erste?"), single(id="q2", question="Zweite?"))
    page = client.get(f"/exams/{exam_id}").text
    assert "Falsch-Pool (" not in page  # no pool list yet
    assert "<fieldset disabled>" in page  # nothing to retry yet

    # An exam attempt with both questions unanswered fills the pool.
    attempt_id = _start(client, exam_id)
    client.post(f"/attempts/{attempt_id}/finish")
    page = client.get(f"/exams/{exam_id}").text
    assert "Falsch-Pool (2)" in page
    assert "0/2" in page

    retry_id = _start(client, exam_id, mode="retry_wrong", feedback="end", count="1")
    with db(client) as session:
        retry = session.get(Attempt, retry_id)
        assert (retry.mode, len(retry.answers)) == ("retry_wrong", 1)
        question_id = retry.answers[0].question_id

    response = client.post(f"/questions/{question_id}/pool/remove", follow_redirects=False)
    assert response.headers["location"] == f"/exams/{exam_id}"
    assert "Falsch-Pool (1)" in client.get(f"/exams/{exam_id}").text


def test_star_and_disable_toggles(client) -> None:
    exam_id = _setup(client, single(), single(id="q2"))
    with db(client) as session:
        q1, q2 = (q.id for q in session.get(Exam, exam_id).questions)

    # Plain form from the exam page: redirects back to the given local path.
    response = client.post(
        f"/questions/{q1}/flags/starred",
        data={"value": "true", "next": f"/exams/{exam_id}#question-{q1}"},
        follow_redirects=False,
    )
    assert response.headers["location"] == f"/exams/{exam_id}#question-{q1}"
    response = client.post(
        f"/questions/{q1}/flags/disabled",
        data={"value": "true", "next": "//evil.example"},
        follow_redirects=False,
    )
    assert response.headers["location"] == f"/exams/{exam_id}"
    page = client.get(f"/exams/{exam_id}").text
    assert "★ Markiert" in page
    assert "Nur markierte Fragen (1)" in page

    # The runner sets flags through the JSON API.
    attempt_id = _start(client, exam_id)
    question = _quiz_data(client.get(f"/attempts/{attempt_id}").text)["questions"][1]
    assert question["flags"] == {"starred": False, "disabled": False}
    response = client.post(question["flagUrls"]["starred"], json={"value": True})
    assert response.json() == {"starred": True}
    assert client.post(f"/api/questions/{q2}/flags/foo", json={"value": True}).status_code == 422

    client.post(f"/attempts/{attempt_id}/finish")
    assert "★ Markiert" in client.get(f"/attempts/{attempt_id}/review").text


def test_resets(client) -> None:
    exam_id = _setup(client, single())
    attempt_id = _start(client, exam_id)
    client.post(f"/attempts/{attempt_id}/finish")
    with db(client) as session:
        question_id = session.get(Exam, exam_id).questions[0].id
    client.post(f"/questions/{question_id}/flags/starred", data={"value": "true"})

    response = client.post(f"/exams/{exam_id}/history/delete", follow_redirects=False)
    assert response.headers["location"] == f"/exams/{exam_id}"
    client.post(f"/exams/{exam_id}/learning/reset")
    with db(client) as session:
        assert session.exec(select(Attempt)).all() == []
        question = session.get(Exam, exam_id).questions[0]
        assert (question.starred, question.in_wrong_pool) == (False, False)

    response = client.post("/learning/reset", follow_redirects=False)
    assert response.headers["location"] == "/"
