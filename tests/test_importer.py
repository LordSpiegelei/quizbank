import json
import shutil
from dataclasses import replace

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.exporter import export_exam
from app.importer import import_directory, import_exam
from app.main import create_app
from app.models import Answer, Attempt, Course, Exam, Question
from app.schemas import parse_exam
from tests.factories import EXAMPLE, import_file, open_q, single

_import = import_file


def test_creates_course_exam_and_questions(session) -> None:
    result = _import(session, single(), single(id="q2", tags=["x"]), time_limit_minutes=30)
    assert (result.created, result.updated, result.removed) == (2, 0, 0)

    exam_row = session.get(Exam, result.exam_id)
    assert exam_row.course.name == "Kurs"
    assert exam_row.time_limit_minutes == 30
    assert [q.external_id for q in sorted(exam_row.questions, key=lambda q: q.position)] == [
        "q1",
        "q2",
    ]


def test_reimport_is_idempotent(session) -> None:
    first = _import(session, single())
    imported_at = session.get(Exam, first.exam_id).imported_at

    second = _import(session, single())
    assert second.exam_id == first.exam_id
    assert (second.created, second.updated, second.removed) == (0, 0, 0)
    assert not second.changed
    assert session.get(Exam, first.exam_id).imported_at == imported_at
    assert len(session.exec(select(Question)).all()) == 1
    assert len(session.exec(select(Course)).all()) == 1


def test_reimport_updates_changed_question_and_keeps_user_flags(session) -> None:
    _import(session, single())
    question = session.exec(select(Question)).one()
    question.starred = True
    session.commit()

    result = _import(session, single(question="Neue Frage?", explanation="Weil."))
    assert (result.created, result.updated) == (0, 1)
    session.refresh(question)
    assert question.question == "Neue Frage?"
    assert question.explanation == "Weil."
    assert question.starred is True


def test_type_change_clears_unused_columns(session) -> None:
    _import(session, single(option_explanations=["a", "b", "c"]))
    _import(session, open_q(id="q1"))
    question = session.exec(select(Question)).one()
    assert question.type == "open"
    assert question.options == []
    assert question.option_explanations is None


def test_questions_missing_from_file_are_deleted_answers_keep_snapshot(session) -> None:
    result = _import(session, single(), single(id="q2"))
    q2 = session.exec(select(Question).where(Question.external_id == "q2")).one()
    attempt = Attempt(exam_id=result.exam_id)
    session.add(attempt)
    session.flush()
    session.add(
        Answer(attempt_id=attempt.id, question_id=q2.id, snapshot={"type": "single"}, selected=[1])
    )
    session.commit()

    result = _import(session, single())
    assert result.removed == 1
    assert [q.external_id for q in session.exec(select(Question))] == ["q1"]
    answer = session.exec(select(Answer)).one()
    assert answer.question_id is None
    assert answer.snapshot == {"type": "single"}
    assert session.get(Attempt, attempt.id) is not None


def test_same_title_in_other_course_is_a_separate_exam(session) -> None:
    a = _import(session, single(), course="Kurs A")
    b = _import(session, single(), course="Kurs B")
    assert a.exam_id != b.exam_id


def test_import_directory_reports_invalid_files(session, tmp_path) -> None:
    shutil.copy(EXAMPLE, tmp_path / "good.json")
    (tmp_path / "bad.json").write_text(json.dumps({"format_version": 1}))

    imports = import_directory(session, tmp_path)
    assert imports["good.json"].created == 7
    assert any(e.startswith("course:") for e in imports["bad.json"])
    assert session.exec(select(Exam)).one().source_file == "good.json"


def test_auto_import_on_startup(settings) -> None:
    settings.questions_dir.mkdir()
    shutil.copy(EXAMPLE, settings.questions_dir / "sample.json")

    for auto_import, expected in ((False, 0), (True, 1), (True, 1)):
        app = create_app(replace(settings, auto_import=auto_import))
        with TestClient(app), Session(app.state.engine) as session:
            assert len(session.exec(select(Exam)).all()) == expected


def test_export_roundtrip(session) -> None:
    expected = parse_exam(EXAMPLE.read_bytes())
    result = import_exam(session, expected, source_file="sample.json")
    exam_row = session.get(Exam, result.exam_id)
    exam_row.course.display_name = "Umbenannt"  # export keeps the import key
    exam_row.questions[0].starred = True  # learning state is not exported
    session.commit()

    exported = export_exam(exam_row)
    assert parse_exam(exported) == expected
    assert '"points": 1,' in exported  # whole numbers without ".0"
    assert import_exam(session, parse_exam(exported), source_file="sample.json").changed is False
