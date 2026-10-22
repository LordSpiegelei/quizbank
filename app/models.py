from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


def _fk(target: str) -> Any:
    """Indexed foreign key; the database deletes children with their parent."""
    return Field(foreign_key=target, ondelete="CASCADE", index=True)


def _children(back_populates: str) -> Any:
    """One-to-many relationship whose children are deleted by ON DELETE CASCADE."""
    return Relationship(back_populates=back_populates, cascade_delete=True, passive_deletes=True)


class Course(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    default_time_limit: int | None = None
    default_pass_mark: float | None = None

    exams: list["Exam"] = _children("course")


class Exam(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("course_id", "title"),)

    id: int | None = Field(default=None, primary_key=True)
    course_id: int = _fk("course.id")
    title: str
    time_limit_minutes: int | None = None
    pass_mark: float | None = None
    multiple_scoring: str = "all_or_nothing"
    source_file: str | None = None
    imported_at: datetime = Field(default_factory=utcnow)

    course: Course = Relationship(back_populates="exams")
    questions: list["Question"] = _children("exam")
    attempts: list["Attempt"] = _children("exam")


class Question(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("exam_id", "external_id"),)

    id: int | None = Field(default=None, primary_key=True)
    external_id: str
    exam_id: int = _fk("exam.id")
    position: int = 0
    type: str
    topic: str
    tags: list[str] = Field(default_factory=list, sa_type=JSON)
    source: str | None = None
    question: str
    scenario: str | None = None
    options: list[str] = Field(default_factory=list, sa_type=JSON)
    correct: list[int] = Field(default_factory=list, sa_type=JSON)
    points: float = 1
    explanation: str | None = None
    option_explanations: list[str] | None = Field(default=None, sa_type=JSON)
    model_answer: str | None = None
    rubric: list[dict[str, Any]] | None = Field(default=None, sa_type=JSON)
    starred: bool = False
    disabled: bool = False

    exam: Exam = Relationship(back_populates="questions")
    answers: list["Answer"] = _children("question")


class Attempt(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    exam_id: int = _fk("exam.id")
    mode: str = "exam"
    filter: dict[str, Any] | None = Field(default=None, sa_type=JSON)
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    duration_seconds: int | None = None
    score: float | None = None
    max_score: float | None = None
    passed: bool | None = None

    exam: Exam = Relationship(back_populates="attempts")
    answers: list["Answer"] = _children("attempt")


class Answer(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("attempt_id", "question_id"),)

    id: int | None = Field(default=None, primary_key=True)
    attempt_id: int = _fk("attempt.id")
    question_id: int = _fk("question.id")
    selected: list[int] | None = Field(default=None, sa_type=JSON)
    text_answer: str | None = None
    self_score: float | None = None
    is_correct: bool | None = None

    attempt: Attempt = Relationship(back_populates="answers")
    question: Question = Relationship(back_populates="answers")
