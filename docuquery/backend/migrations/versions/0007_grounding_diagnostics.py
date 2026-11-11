"""Grounding diagnostics; private draft storage is controlled by settings."""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("query_logs", sa.Column("refusal_reason", sa.String(64), nullable=True))
    op.add_column("query_logs", sa.Column("draft_answer", sa.Text(), nullable=True))
    op.add_column("query_logs", sa.Column("prompt_chars", sa.Integer(), nullable=True))


def downgrade():
    for name in ("prompt_chars", "draft_answer", "refusal_reason"):
        op.drop_column("query_logs", name)
