"""Degraded-ingestion flag: documents indexed keyword-only when embedding fails.

revision = "0008"
down_revision = "0007"
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "documents",
        sa.Column("degraded", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade():
    op.drop_column("documents", "degraded")
