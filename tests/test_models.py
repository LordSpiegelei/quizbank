import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.models import Answer, Attempt, Course, Exam, Question


def _seed(session) -> Exam:
    course = Course(name="Example Course", default_pass_mark=30)
    exam = Exam(course=course, title="Probeklausur 1", time_limit_minutes=120)
    question = Question(
        exam=exam,
        external_id="ex-p1-001",
        type="single",
        topic="Strategie",
        tags=["Porter"],
        question="Welche Aussage …?",
        options=["A", "B", "C"],
        correct=[1],
        option_explanations=["a", "b", "c"],
    )
    attempt = Attempt(exam=exam)
    session.add_all([course, exam, question, attempt])
    session.flush()
    session.add(Answer(attempt=attempt, question=question, selected=[1], is_correct=True))
    session.commit()
    return exam


def test_migration_creates_all_tables(engine) -> None:
    tables = set(inspect(engine).get_table_names())
    assert {"course", "exam", "question", "attempt", "answer", "alembic_version"} <= tables


def test_json_columns_roundtrip(session) -> None:
    _seed(session)
    session.expire_all()
    question = session.exec(select(Question)).one()
    assert question.tags == ["Porter"]
    assert question.correct == [1]
    assert question.rubric is None


def test_deleting_course_cascades(session) -> None:
    _seed(session)
    session.delete(session.exec(select(Course)).one())
    session.commit()
    for model in (Exam, Question, Attempt, Answer):
        assert session.exec(select(model)).all() == []


def test_database_level_cascade(session) -> None:
    """ON DELETE CASCADE must also work for bulk deletes that bypass the ORM."""
    exam = _seed(session)
    session.connection().exec_driver_sql(f"DELETE FROM exam WHERE id = {exam.id}")
    session.commit()
    assert session.exec(select(Answer)).all() == []


def test_external_id_unique_per_exam(session) -> None:
    exam = _seed(session)
    session.add(
        Question(exam_id=exam.id, external_id="ex-p1-001", type="single", topic="x", question="dup")
    )
    with pytest.raises(IntegrityError):
        session.commit()
