"""multi-format ingestion: chunk source locations and document source URLs

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Non-PDF formats cite a slide/sheet/heading instead of a page number.
    op.add_column("chunks", sa.Column("location", sa.String(255), nullable=True))
    # Documents imported from a URL remember where they came from.
    op.add_column("documents", sa.Column("source_url", sa.String(2048), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "source_url")
    op.drop_column("chunks", "location")
