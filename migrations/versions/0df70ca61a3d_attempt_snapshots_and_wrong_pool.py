"""attempt snapshots and wrong pool

Revision ID: 0df70ca61a3d
Revises: c84e9ed06c2f
Create Date: 2026-10-23 11:56:24.893462

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0df70ca61a3d"
down_revision: str | Sequence[str] | None = "c84e9ed06c2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The initial schema created unnamed foreign keys; batch mode reflects them under these names.
NAMING = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}
ANSWER_QUESTION_FK = "fk_answer_question_id_question"


def upgrade() -> None:
    """Upgrade schema."""
    # Existing rows need server defaults for the new NOT NULL columns.
    with op.batch_alter_table("answer", naming_convention=NAMING) as batch_op:
        batch_op.add_column(sa.Column("position", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("snapshot", sa.JSON(), nullable=False, server_default="{}"))
        batch_op.add_column(sa.Column("rubric_scores", sa.JSON(), nullable=True))
        batch_op.add_column(
            sa.Column("revealed", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.alter_column("question_id", existing_type=sa.INTEGER(), nullable=True)
        batch_op.drop_constraint(ANSWER_QUESTION_FK, type_="foreignkey")
        batch_op.create_foreign_key(
            ANSWER_QUESTION_FK, "question", ["question_id"], ["id"], ondelete="SET NULL"
        )

    with op.batch_alter_table("attempt") as batch_op:
        batch_op.add_column(
            sa.Column(
                "feedback", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default="end"
            )
        )
        batch_op.add_column(sa.Column("pass_mark", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("time_limit_minutes", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "multiple_scoring",
                sqlmodel.sql.sqltypes.AutoString(),
                nullable=False,
                server_default="all_or_nothing",
            )
        )
        batch_op.add_column(
            sa.Column("deadline", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True)
        )

    with op.batch_alter_table("course") as batch_op:
        batch_op.add_column(
            sa.Column("display_name", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
        )

    with op.batch_alter_table("question") as batch_op:
        batch_op.add_column(
            sa.Column("in_wrong_pool", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(
            sa.Column("pool_streak", sa.Integer(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("question") as batch_op:
        batch_op.drop_column("pool_streak")
        batch_op.drop_column("in_wrong_pool")

    with op.batch_alter_table("course") as batch_op:
        batch_op.drop_column("display_name")

    with op.batch_alter_table("attempt") as batch_op:
        batch_op.drop_column("deadline")
        batch_op.drop_column("multiple_scoring")
        batch_op.drop_column("time_limit_minutes")
        batch_op.drop_column("pass_mark")
        batch_op.drop_column("feedback")

    # Answers whose question was deleted cannot satisfy NOT NULL again.
    op.execute("DELETE FROM answer WHERE question_id IS NULL")
    with op.batch_alter_table("answer", naming_convention=NAMING) as batch_op:
        batch_op.drop_constraint(ANSWER_QUESTION_FK, type_="foreignkey")
        batch_op.create_foreign_key(
            ANSWER_QUESTION_FK, "question", ["question_id"], ["id"], ondelete="CASCADE"
        )
        batch_op.alter_column("question_id", existing_type=sa.INTEGER(), nullable=False)
        batch_op.drop_column("revealed")
        batch_op.drop_column("rubric_scores")
        batch_op.drop_column("snapshot")
        batch_op.drop_column("position")
