"""Shared FastAPI dependencies: authentication, rate limiting, and database access."""

from collections import defaultdict
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
import logging
import time
import uuid as uuidlib

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.db.session import SessionLocal, get_db
from app.services.auth import decode_access_token

logger = logging.getLogger(__name__)


async def get_current_user_optional(
    authorization: str | None = Header(None),
) -> User | None:
    """Extract and authenticate the user if Bearer token is provided.

    Returns None for guest/unauthenticated requests (allowing public portfolio demo).
    Avoids checkout from DB pool if no Authorization header is passed.
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

    try:
        user_id = uuidlib.UUID(payload["sub"])
        async with SessionLocal() as db:
            user = await db.get(User, user_id)
            return user
    except Exception as exc:
        logger.debug("Failed to resolve user from token: %s", exc)
        return None


async def get_current_user(
    user: User | None = Depends(get_current_user_optional),
) -> User:
    """Require an authenticated user. Raises 401 if not logged in."""
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


class RateLimiter:
    """In-memory sliding window rate limiter per client IP."""

    def __init__(self, max_requests: int = 60, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests: dict[str, list[float]] = defaultdict(list)

    async def __call__(self, request: Request) -> None:
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        window_start = now - self.window_seconds

        # Clean old timestamps
        timestamps = self.requests[client_ip]
        self.requests[client_ip] = [t for t in timestamps if t > window_start]

        if len(self.requests[client_ip]) >= self.max_requests:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded: maximum {self.max_requests} requests per {self.window_seconds}s.",
            )

        self.requests[client_ip].append(now)


# Standard rate limiters
rate_limit_chat = RateLimiter(max_requests=30, window_seconds=60)
rate_limit_upload = RateLimiter(max_requests=20, window_seconds=60)
