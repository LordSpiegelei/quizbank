"""initial schema

Revision ID: c84e9ed06c2f
Revises:
Create Date: 2026-10-22 19:46:46.329409

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c84e9ed06c2f"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "course",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("default_time_limit", sa.Integer(), nullable=True),
        sa.Column("default_pass_mark", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("course", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_course_name"), ["name"], unique=True)

    op.create_table(
        "exam",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("time_limit_minutes", sa.Integer(), nullable=True),
        sa.Column("pass_mark", sa.Float(), nullable=True),
        sa.Column("multiple_scoring", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("source_file", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("imported_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["course_id"], ["course.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("course_id", "title"),
    )
    with op.batch_alter_table("exam", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_exam_course_id"), ["course_id"], unique=False)

    op.create_table(
        "attempt",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("exam_id", sa.Integer(), nullable=False),
        sa.Column("mode", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("filter", sa.JSON(), nullable=True),
        sa.Column("started_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("finished_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("max_score", sa.Float(), nullable=True),
        sa.Column("passed", sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(["exam_id"], ["exam.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("attempt", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_attempt_exam_id"), ["exam_id"], unique=False)

    op.create_table(
        "question",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("external_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("exam_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("topic", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("source", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("question", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("scenario", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("correct", sa.JSON(), nullable=False),
        sa.Column("points", sa.Float(), nullable=False),
        sa.Column("explanation", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("option_explanations", sa.JSON(), nullable=True),
        sa.Column("model_answer", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("rubric", sa.JSON(), nullable=True),
        sa.Column("starred", sa.Boolean(), nullable=False),
        sa.Column("disabled", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["exam_id"], ["exam.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("exam_id", "external_id"),
    )
    with op.batch_alter_table("question", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_question_exam_id"), ["exam_id"], unique=False)

    op.create_table(
        "answer",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("attempt_id", sa.Integer(), nullable=False),
        sa.Column("question_id", sa.Integer(), nullable=False),
        sa.Column("selected", sa.JSON(), nullable=True),
        sa.Column("text_answer", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("self_score", sa.Float(), nullable=True),
        sa.Column("is_correct", sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(["attempt_id"], ["attempt.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["question_id"], ["question.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("attempt_id", "question_id"),
    )
    with op.batch_alter_table("answer", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_answer_attempt_id"), ["attempt_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_answer_question_id"), ["question_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("answer", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_answer_question_id"))
        batch_op.drop_index(batch_op.f("ix_answer_attempt_id"))

    op.drop_table("answer")
    with op.batch_alter_table("question", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_question_exam_id"))

    op.drop_table("question")
    with op.batch_alter_table("attempt", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_attempt_exam_id"))

    op.drop_table("attempt")
    with op.batch_alter_table("exam", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_exam_course_id"))

    op.drop_table("exam")
    with op.batch_alter_table("course", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_course_name"))

    op.drop_table("course")
