"""structured extraction jobs (professional edition feature)

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-31
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "extraction_jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column(
            "status",
            sa.Enum("pending", "running", "completed", "failed", name="extraction_job_status"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("template", sa.String(64), nullable=False, server_default="custom"),
        sa.Column("document_ids", sa.JSON(), nullable=True),
        sa.Column("schema_json", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("confidence", sa.String(16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_extraction_jobs_owner_id", "extraction_jobs", ["owner_id"])
    op.create_index("ix_extraction_jobs_status", "extraction_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_extraction_jobs_status", table_name="extraction_jobs")
    op.drop_index("ix_extraction_jobs_owner_id", table_name="extraction_jobs")
    op.drop_table("extraction_jobs")
    sa.Enum(name="extraction_job_status").drop(op.get_bind(), checkfirst=True)
