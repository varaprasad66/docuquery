"""Recoverable object cleanup."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("storage_cleanup",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("locator", sa.String(1024), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))

def downgrade():
    op.drop_table("storage_cleanup")
