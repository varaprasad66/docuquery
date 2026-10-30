"""Shared FastAPI dependencies: authentication, rate limiting, and database access."""

import logging
import uuid as uuidlib

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.db.session import SessionLocal, get_db
from app.services.auth import decode_access_token
from app.services.rate_limit import rate_limit_dependency
from app.services.token_store import token_blacklist

logger = logging.getLogger(__name__)


async def get_current_user_optional(
    authorization: str | None = None,
) -> User | None:
    """Extract and authenticate the user if a valid Bearer token is provided.

    Returns None for guest/unauthenticated requests (allowing public portfolio demo).
    A revoked token (logged-out jti) or deactivated user is treated as anonymous.
    """
    if not authorization:
        return None

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None

    token = parts[1]
    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        return None

    jti = payload.get("jti")
    if jti and token_blacklist.is_revoked(jti):
        return None

    try:
        user_id = uuidlib.UUID(payload["sub"])
        async with SessionLocal() as db:
            user = await db.get(User, user_id)
            if user is None or not user.is_active:
                return None
            return user
    except Exception as exc:
        logger.debug("Failed to resolve user from token: %s", exc)
        return None


def _optional_user_from_request_factory():
    """Build a FastAPI dependency that pulls the bearer token from the request."""
    from fastapi import Header

    async def dep(authorization: str | None = Header(None)) -> User | None:
        return await get_current_user_optional(authorization)

    return dep


get_current_user_optional_dep = _optional_user_from_request_factory()


async def get_current_user(
    user: User | None = Depends(get_current_user_optional_dep),
) -> User:
    """Require an authenticated user. Raises 401 if not logged in."""
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def get_admin_user(user: User = Depends(get_current_user)) -> User:
    """Require an authenticated admin. Non-admins receive 404 (surface exists but is
    not advertised)."""
    from app.db.models import UserRole

    if user.role != UserRole.admin.value:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")
    return user


# Configurable rate limiters (Redis-backed with in-process fallback)
rate_limit_chat = rate_limit_dependency("chat")
rate_limit_upload = rate_limit_dependency("upload")
rate_limit_search = rate_limit_dependency("search")
rate_limit_auth = rate_limit_dependency("auth")


async def client_identity(request: Request) -> tuple[str | None, str | None]:
    """(ip, user_agent) for audit rows."""
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent", "")[:512] or None
    return ip, ua
