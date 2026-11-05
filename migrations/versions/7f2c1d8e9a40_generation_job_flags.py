"""generation job flags

Revision ID: 7f2c1d8e9a40
Revises: e3a6d9f04b17
Create Date: 2026-11-05 21:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7f2c1d8e9a40"
down_revision: str | Sequence[str] | None = "e3a6d9f04b17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("generationjob") as batch_op:
        batch_op.add_column(sa.Column("flags", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("generationjob") as batch_op:
        batch_op.drop_column("flags")
