from datetime import UTC, datetime
from typing import Any

from pydantic import ConfigDict
from sqlalchemy import JSON, ColumnElement, UniqueConstraint, func
from sqlalchemy.ext.hybrid import hybrid_property
from sqlmodel import AutoString, Field, Relationship, SQLModel

from app.schemas import MultipleScoring


def utcnow() -> datetime:
    return datetime.now(UTC)


def _fk(target: str) -> Any:
    """Indexed foreign key; the database deletes children with their parent."""
    return Field(foreign_key=target, ondelete="CASCADE", index=True)


def _children(back_populates: str, order_by: str | None = None) -> Any:
    """One-to-many relationship whose children are deleted by ON DELETE CASCADE."""
    return Relationship(
        back_populates=back_populates,
        cascade_delete=True,
        passive_deletes=True,
        sa_relationship_kwargs={"order_by": order_by} if order_by else {},
    )


class Course(SQLModel, table=True):
    model_config = ConfigDict(ignored_types=(hybrid_property,))  # let pydantic skip `label`

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)  # import key: exam files match on it
    display_name: str | None = None
    default_time_limit: int | None = None
    default_pass_mark: float | None = None

    exams: list["Exam"] = _children("course")

    @hybrid_property
    def label(self) -> str:
        """The name shown in the UI: the display name, falling back to the import key."""
        return self.display_name or self.name

    @label.inplace.expression
    @classmethod
    def _label_expression(cls) -> ColumnElement[str]:
        return func.coalesce(cls.display_name, cls.name)

    def rename(self, label: str) -> None:
        """Files match on `name`, so renaming only sets the display name (CLAUDE.md §5)."""
        self.display_name = None if label == self.name else label


class Exam(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("course_id", "title"),)

    id: int | None = Field(default=None, primary_key=True)
    course_id: int = _fk("course.id")
    title: str
    time_limit_minutes: int | None = None
    pass_mark: float | None = None
    multiple_scoring: MultipleScoring = Field(default="all_or_nothing", sa_type=AutoString)
    source_file: str | None = None
    imported_at: datetime = Field(default_factory=utcnow)

    course: Course = Relationship(back_populates="exams")
    questions: list["Question"] = _children("exam", order_by="Question.position")
    attempts: list["Attempt"] = _children("exam")

    @property
    def effective_time_limit(self) -> int | None:
        """Own time limit, falling back to the course default."""
        own = self.time_limit_minutes
        return own if own is not None else self.course.default_time_limit

    @property
    def effective_pass_mark(self) -> float | None:
        """Own pass mark, falling back to the course default."""
        own = self.pass_mark
        return own if own is not None else self.course.default_pass_mark


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
    # Local learning state; the importer never touches these.
    starred: bool = False
    disabled: bool = False
    in_wrong_pool: bool = False
    pool_streak: int = 0

    exam: Exam = Relationship(back_populates="questions")
    # Answers keep their snapshot when the question is deleted (question_id becomes NULL).
    answers: list["Answer"] = Relationship(back_populates="question", passive_deletes=True)


class Attempt(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    exam_id: int = _fk("exam.id")
    mode: str = "exam"
    feedback: str = "end"
    filter: dict[str, Any] | None = Field(default=None, sa_type=JSON)
    # Effective exam settings, frozen at start so later changes leave results alone.
    # pass_mark is only set for exam mode: pass/fail is meaningless for partial attempts.
    pass_mark: float | None = None
    time_limit_minutes: int | None = None
    multiple_scoring: MultipleScoring = Field(default="all_or_nothing", sa_type=AutoString)
    deadline: datetime | None = None
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    duration_seconds: int | None = None
    score: float | None = None
    max_score: float | None = None
    passed: bool | None = None

    exam: Exam = Relationship(back_populates="attempts")
    answers: list["Answer"] = _children("attempt", order_by="Answer.position")

    @property
    def percent(self) -> float:
        """Score as a percentage of the maximum (0 for an attempt without points)."""
        return 100 * (self.score or 0) / self.max_score if self.max_score else 0.0


class Answer(SQLModel, table=True):
    """One row per question in an attempt, created when the attempt starts."""

    __table_args__ = (UniqueConstraint("attempt_id", "question_id"),)

    id: int | None = Field(default=None, primary_key=True)
    attempt_id: int = _fk("attempt.id")
    question_id: int | None = Field(
        default=None, foreign_key="question.id", ondelete="SET NULL", index=True
    )
    position: int = 0
    # The question as it was at start, already in display order; `selected` indexes into it.
    snapshot: dict[str, Any] = Field(default_factory=dict, sa_type=JSON)
    selected: list[int] | None = Field(default=None, sa_type=JSON)
    text_answer: str | None = None
    rubric_scores: list[float] | None = Field(default=None, sa_type=JSON)
    self_score: float | None = None
    revealed: bool = False
    is_correct: bool | None = None

    attempt: Attempt = Relationship(back_populates="answers")
    question: Question | None = Relationship(back_populates="answers")
