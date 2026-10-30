"""Audit trail: security-relevant events recorded with actor, resource, and origin."""

import logging
import uuid as uuidlib

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog

logger = logging.getLogger(__name__)


async def record_audit(
    db: AsyncSession,
    action: str,
    *,
    actor_id: uuidlib.UUID | None = None,
    actor_email: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    detail: dict | None = None,
) -> None:
    """Add an audit row to the caller's session (committed with the caller's work).
    Never raises: an audit failure must not break the user-facing operation."""
    try:
        db.add(
            AuditLog(
                actor_id=actor_id,
                actor_email=actor_email,
                action=action,
                resource_type=resource_type,
                resource_id=str(resource_id) if resource_id else None,
                ip=ip,
                user_agent=user_agent,
                detail=detail,
            )
        )
    except Exception:
        logger.warning("Failed to stage audit row for action %s", action, exc_info=True)
