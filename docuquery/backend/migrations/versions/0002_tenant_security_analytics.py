"""tenant security, roles, audit log, refresh tokens, semantic cache, feedback, costs

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import UUID

from app.config import get_settings

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Documents: visibility (tenant isolation support) ---
    op.add_column(
        "documents",
        sa.Column("visibility", sa.String(16), nullable=False, server_default="public"),
    )
    op.create_index("ix_documents_visibility", "documents", ["visibility"])

    # --- Users: role + activation state ---
    op.add_column(
        "users",
        sa.Column("role", sa.String(16), nullable=False, server_default="user"),
    )
    op.add_column(
        "users",
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
    )

    # --- Chunks: embedding model versioning (future reindex support) ---
    op.add_column("chunks", sa.Column("embedding_model", sa.String(255), nullable=True))

    # --- Messages: answer feedback loop ---
    op.add_column("messages", sa.Column("feedback_rating", sa.String(8), nullable=True))
    op.add_column("messages", sa.Column("feedback_comment", sa.Text(), nullable=True))

    # --- Query logs: per-user scoping, cache hits, cost tracking ---
    op.add_column(
        "query_logs",
        sa.Column(
            "owner_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "query_logs",
        sa.Column("cached", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "query_logs",
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
    )
    op.create_index("ix_query_logs_owner_id", "query_logs", ["owner_id"])

    # --- Refresh tokens (rotation + theft detection) ---
    op.create_table(
        "refresh_tokens",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(128), nullable=False, unique=True),
        sa.Column("family_id", UUID(as_uuid=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index("ix_refresh_tokens_family_id", "refresh_tokens", ["family_id"])

    # --- Audit log (enterprise) ---
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("actor_id", UUID(as_uuid=True), nullable=True),
        sa.Column("actor_email", sa.String(320), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=True),
        sa.Column("resource_id", sa.String(64), nullable=True),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
    )
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_actor_id", "audit_logs", ["actor_id"])

    # --- Semantic answer cache (professional) ---
    op.create_table(
        "semantic_cache",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("scope_hash", sa.String(64), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("question_embedding", Vector(get_settings().embed_dim), nullable=True),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=True),
        sa.Column("methods", sa.JSON(), nullable=True),
        sa.Column("model", sa.String(255), nullable=False, server_default=""),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_semantic_cache_scope_hash", "semantic_cache", ["scope_hash"])
    op.create_index("ix_semantic_cache_expires_at", "semantic_cache", ["expires_at"])
    op.execute(
        "CREATE INDEX ix_semantic_cache_embedding ON semantic_cache USING hnsw "
        "(question_embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.drop_table("semantic_cache")
    op.drop_table("audit_logs")
    op.drop_table("refresh_tokens")
    op.drop_index("ix_query_logs_owner_id", table_name="query_logs")
    op.drop_column("query_logs", "cost_usd")
    op.drop_column("query_logs", "cached")
    op.drop_column("query_logs", "owner_id")
    op.drop_column("messages", "feedback_comment")
    op.drop_column("messages", "feedback_rating")
    op.drop_column("chunks", "embedding_model")
    op.drop_column("users", "is_active")
    op.drop_column("users", "role")
    op.drop_index("ix_documents_visibility", table_name="documents")
    op.drop_column("documents", "visibility")
