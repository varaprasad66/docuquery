"""Shared FastAPI dependencies: authentication (JWT + API keys), rate limiting,
and database access."""

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

API_KEY_PREFIX = "dq_"


async def _user_from_jwt(token: str) -> User | None:
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


async def get_current_user_optional(
    authorization: str | None = None,
) -> tuple[User | None, list[str] | None]:
    """Authenticate the caller. Returns (user, api_key_scopes).

    - JWT bearer token: (user, None) — sessions implicitly hold every scope.
    - API key (dq_-prefixed bearer): (user, [scopes]); unknown or revoked keys
      fail CLOSED with 401 — a programmatic client must never silently
      downgrade to guest access.
    - Missing header: (None, None) — guest/demo mode.
    """
    if not authorization:
        return None, None

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None, None
    token = parts[1]

    if token.startswith(API_KEY_PREFIX):
        from app.services.api_keys import authenticate

        async with SessionLocal() as db:
            resolved = await authenticate(db, token)
        if resolved is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or revoked API key.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        user, api_key = resolved
        return user, list(api_key.scopes or [])

    return await _user_from_jwt(token), None


def _optional_user_from_request_factory():
    """Build a FastAPI dependency that pulls the credential from the request and
    records the auth context (for API-key scope checks) on request.state."""
    from fastapi import Header

    async def dep(request: Request, authorization: str | None = Header(None)) -> User | None:
        user, scopes = await get_current_user_optional(authorization)
        request.state.auth_scopes = scopes
        return user

    return dep


get_current_user_optional_dep = _optional_user_from_request_factory()


def require_api_key_scope(scope: str):
    """Endpoint dependency: when the caller used an API key, it must hold the
    given scope. JWT sessions pass unconditionally. Declares the auth
    dependency itself so the auth context is resolved before this check."""

    async def dep(
        request: Request,
        user: User | None = Depends(get_current_user_optional_dep),
    ) -> None:
        scopes = getattr(request.state, "auth_scopes", None)
        if scopes is not None and scope not in scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This API key does not have the '{scope}' scope.",
            )

    return dep


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


async def require_jwt_session(
    request: Request,
    user: User = Depends(get_current_user),
) -> User:
    """Require a JWT session specifically: API keys may consume the product API
    but can never manage keys or webhooks."""
    if getattr(request.state, "auth_scopes", None) is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API keys cannot manage keys or webhooks. Use your JWT session.",
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
