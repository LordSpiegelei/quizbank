"""answer ai review

Revision ID: 5b1f0c2a7d3e
Revises: 0df70ca61a3d
Create Date: 2026-11-05 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5b1f0c2a7d3e"
down_revision: str | Sequence[str] | None = "0df70ca61a3d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("answer") as batch_op:
        batch_op.add_column(
            sa.Column("ai_feedback", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
        )
        batch_op.add_column(sa.Column("ai_scores", sa.JSON(), nullable=True))
        batch_op.add_column(
            sa.Column("ai_model", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("ai_reviewed_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True)
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("answer") as batch_op:
        batch_op.drop_column("ai_reviewed_at")
        batch_op.drop_column("ai_model")
        batch_op.drop_column("ai_scores")
        batch_op.drop_column("ai_feedback")
