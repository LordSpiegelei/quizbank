"""generation job

Revision ID: 9c4e7a1b2d68
Revises: 5b1f0c2a7d3e
Create Date: 2026-11-05 11:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9c4e7a1b2d68"
down_revision: str | Sequence[str] | None = "5b1f0c2a7d3e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "generationjob",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("model", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("material_text", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("images", sa.JSON(), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("draft", sa.JSON(), nullable=True),
        sa.Column("errors", sa.JSON(), nullable=False),
        sa.Column("regenerating", sa.JSON(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("updated_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("generationjob")
