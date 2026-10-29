from dataclasses import replace
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlmodel import select

from app.main import create_app
from app.models import Attempt, Course, Exam, Question
from tests.factories import EXAMPLE, exam, single
from tests.factories import db as _db
from tests.factories import upload as _upload


def test_empty_course_list(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Noch keine Kurse" in response.text


def test_static_assets_are_served(client) -> None:
    assert client.get("/static/vendor/pico.min.css").status_code == 200
    assert client.get("/static/app.css").status_code == 200
    assert client.get("/static/theme.js").status_code == 200
    assert 'id="theme-toggle"' in client.get("/").text


def test_upload_valid_file(client) -> None:
    response = _upload(client, ("sample.json", EXAMPLE.read_bytes()))
    assert response.status_code == 200
    assert "Probeklausur 1: 7 neu, 0 aktualisiert, 0 entfernt." in response.text

    response = _upload(client, ("sample.json", EXAMPLE.read_bytes()))
    assert "Probeklausur 1: unverändert." in response.text

    with _db(client) as session:
        assert session.exec(select(Exam)).one().source_file == "sample.json"


def test_upload_reports_errors_per_file(client) -> None:
    response = _upload(
        client,
        ("good.json", exam()),
        ("bad.json", exam(single(correct=[9]))),
    )
    assert response.status_code == 200
    assert "Kurs / Klausur: 1 neu" in response.text
    assert "questions[0].correct: index 9 out of range" in response.text


def test_upload_without_file(client) -> None:
    response = client.post("/import", files=[("files", ("", b"", "application/octet-stream"))])
    assert response.status_code == 400
    assert "mindestens eine Datei" in response.text


def test_course_and_exam_pages(client) -> None:
    _upload(client, ("sample.json", EXAMPLE.read_bytes()))
    with _db(client) as session:
        course = session.exec(select(Course)).one()
        exam_row = session.exec(select(Exam)).one()

    listing = client.get("/").text
    assert "Beispielkurs Grundlagen der Informatik" in listing
    assert "noch nie" in listing

    page = client.get(f"/courses/{course.id}").text
    assert "Probeklausur 1" in page
    assert "20 Min." in page

    page = client.get(f"/exams/{exam_row.id}").text
    assert "11 Punkte" in page  # max score of the example
    assert "Datenstrukturen, Fallstudie, Zahlensysteme" in page.replace("Algorithmen, ", "")

    assert client.get("/courses/999").status_code == 404
    assert client.get("/exams/999").status_code == 404


def test_last_result_and_recent_sort(client) -> None:
    _upload(client, ("a.json", exam(course="Alpha")), ("b.json", exam(course="Beta")))
    with _db(client) as session:
        beta_exam = session.exec(select(Exam).join(Course).where(Course.name == "Beta")).one()
        now = datetime.now(UTC)
        session.add(
            Attempt(
                exam_id=beta_exam.id,
                started_at=now,
                finished_at=now,
                score=1,
                max_score=1,
                passed=True,
            )
        )
        session.commit()
        beta_id = beta_exam.course_id

    by_name = client.get("/").text
    assert by_name.index("Alpha") < by_name.index("Beta")
    by_recent = client.get("/?sort=recent").text
    assert by_recent.index("Beta") < by_recent.index("Alpha")

    assert "bestanden" in client.get(f"/courses/{beta_id}").text


def test_search_is_case_insensitive_with_umlauts(client) -> None:
    client.post("/courses", data={"name": "Ökonomie"})
    client.post("/courses", data={"name": "Statistik"})
    page = client.get("/?q=ökon").text
    assert "Ökonomie" in page
    assert "Statistik" not in page
    assert "Kein Kurs passt" in client.get("/?q=xyz").text


def test_create_course(client) -> None:
    response = client.post("/courses", data={"name": "  Statistik  "}, follow_redirects=False)
    assert response.status_code == 303
    assert "Statistik" in client.get(response.headers["location"]).text

    response = client.post("/courses", data={"name": "Statistik"})
    assert response.status_code == 400
    assert "bereits einen Kurs" in response.text

    assert client.post("/courses", data={"name": " "}).status_code == 400


def test_update_course_settings(client) -> None:
    _upload(client, ("a.json", exam()))  # exam without own time limit / pass mark
    with _db(client) as session:
        course_id = session.exec(select(Course)).one().id
        exam_id = session.exec(select(Exam)).one().id

    response = client.post(
        f"/courses/{course_id}",
        data={"name": "Kurs neu", "default_time_limit": "90", "default_pass_mark": "7,5"},
    )
    assert response.status_code == 200
    assert "Gespeichert." in response.text
    with _db(client) as session:
        course = session.get(Course, course_id)
        # Renaming only sets the display name; files still match on the import key.
        assert (course.name, course.display_name) == ("Kurs", "Kurs neu")
        assert (course.default_time_limit, course.default_pass_mark) == (90, 7.5)

    page = client.get(f"/exams/{exam_id}").text
    assert "90 Min." in page
    assert "7,5 Punkte" in page
    assert "(Kursstandard)" in page

    response = client.post(f"/courses/{course_id}", data={"name": "X", "default_time_limit": "abc"})
    assert response.status_code == 400
    assert "„abc“ ist keine gültige Zahl." in response.text

    client.post(f"/courses/{course_id}", data={"name": "Kurs neu", "default_time_limit": ""})
    with _db(client) as session:
        assert session.get(Course, course_id).default_time_limit is None


def test_delete_course_and_exam(client) -> None:
    _upload(client, ("a.json", exam()), ("b.json", exam(title="Klausur 2")))
    with _db(client) as session:
        course_id = session.exec(select(Course)).one().id
        exam_id = session.exec(select(Exam).where(Exam.title == "Klausur 2")).one().id

    response = client.post(f"/exams/{exam_id}/delete", follow_redirects=False)
    assert response.headers["location"] == f"/courses/{course_id}"
    with _db(client) as session:
        assert len(session.exec(select(Exam)).all()) == 1

    response = client.post(f"/courses/{course_id}/delete", follow_redirects=False)
    assert response.headers["location"] == "/"
    with _db(client) as session:
        assert session.exec(select(Course)).all() == []
        assert session.exec(select(Question)).all() == []


def test_rename_keeps_import_key(client) -> None:
    _upload(client, ("a.json", exam()))
    with _db(client) as session:
        course_id = session.exec(select(Course)).one().id
    client.post("/courses", data={"name": "Statistik"})

    client.post(f"/courses/{course_id}", data={"name": "Mein Kurs"})
    page = client.get(f"/courses/{course_id}").text
    assert "<h1>Mein Kurs</h1>" in page
    assert "über den Namen „Kurs“ zugeordnet" in page
    assert "Mein Kurs" in client.get("/?q=mein").text

    # A re-import still lands in the renamed course instead of creating a new one.
    _upload(client, ("b.json", exam(title="Klausur 2")))
    with _db(client) as session:
        assert len(session.exec(select(Course)).all()) == 2
        assert len(session.get(Course, course_id).exams) == 2

    # Neither another course's displayed name nor its import key may be reused.
    for taken in ("Statistik", "Kurs"):
        response = client.post("/courses", data={"name": taken})
        assert "bereits einen Kurs" in response.text
    response = client.post(f"/courses/{course_id}", data={"name": "Statistik"})
    assert "bereits einen Kurs" in response.text

    client.post(f"/courses/{course_id}", data={"name": "Kurs"})  # back to the import key
    with _db(client) as session:
        assert session.get(Course, course_id).display_name is None


def test_delete_warns_when_file_comes_back(settings) -> None:
    settings.questions_dir.mkdir()
    (settings.questions_dir / "a.json").write_text(exam(), encoding="utf-8")
    app = create_app(replace(settings, auto_import=True))
    with TestClient(app) as client:
        _upload(client, ("b.json", exam(title="Hochgeladen")))
        with _db(client) as session:
            course_id = session.exec(select(Course)).one().id
            uploaded, file_exam = sorted(session.exec(select(Exam)), key=lambda e: e.title)

        warning = "liegt noch im Fragen-Ordner"
        assert warning in client.get(f"/exams/{file_exam.id}").text
        assert warning not in client.get(f"/exams/{uploaded.id}").text
        page = client.get(f"/courses/{course_id}").text
        assert "neu importiert: Klausur" in page


def test_export_download(client) -> None:
    _upload(client, ("Mein Export.json", exam(single(question="Ä?"))))
    with _db(client) as session:
        exam_id = session.exec(select(Exam)).one().id
    response = client.get(f"/exams/{exam_id}/export")
    assert response.headers["content-type"] == "application/json"
    assert "filename*=UTF-8''Mein%20Export.json" in response.headers["content-disposition"]
    assert '"question": "Ä?"' in response.text
    assert "Als JSON exportieren" in client.get(f"/exams/{exam_id}").text
