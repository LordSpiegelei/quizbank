"""generation job log

Revision ID: e3a6d9f04b17
Revises: 9c4e7a1b2d68
Create Date: 2026-11-05 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e3a6d9f04b17"
down_revision: str | Sequence[str] | None = "9c4e7a1b2d68"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("generationjob") as batch_op:
        batch_op.add_column(sa.Column("log", sa.JSON(), nullable=False, server_default="[]"))
        batch_op.add_column(sa.Column("live", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("generationjob") as batch_op:
        batch_op.drop_column("live")
        batch_op.drop_column("log")
