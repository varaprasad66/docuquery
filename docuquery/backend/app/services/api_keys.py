"""API key generation, hashing, and authentication.

The raw key (prefix `dq_`) is shown exactly once at creation; only its SHA-256
hash is stored. Keys carry scopes (read | chat | upload); JWT sessions
implicitly hold every scope. Revoked or unknown keys fail closed with 401.
"""

import hashlib
import secrets
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiKey, User

KEY_PREFIX = "dq_"
SCOPES = ("read", "chat", "upload")


def generate_api_key() -> tuple[str, str, str]:
    """Returns (raw_key, display_prefix, key_hash). The raw key is never stored."""
    raw = KEY_PREFIX + secrets.token_urlsafe(24)
    prefix = raw[:11]  # "dq_" + 8 chars, enough to identify, useless to forge
    return raw, prefix, hash_key(raw)


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def normalize_scopes(scopes: list[str] | None) -> list[str]:
    """Validate and de-duplicate requested scopes; empty -> ["read"]."""
    if not scopes:
        return ["read"]
    cleaned: list[str] = []
    for scope in scopes:
        if scope not in SCOPES:
            raise ValueError(f"Unknown scope '{scope}'. Valid: {', '.join(SCOPES)}.")
        if scope not in cleaned:
            cleaned.append(scope)
    return cleaned


async def authenticate(db: AsyncSession, raw_key: str) -> tuple[User, ApiKey] | None:
    """Resolve a raw API key to an active user. None if unknown or revoked."""
    result = await db.execute(select(ApiKey).where(ApiKey.key_hash == hash_key(raw_key)))
    api_key = result.scalar_one_or_none()
    if api_key is None or api_key.revoked_at is not None:
        return None
    user = await db.get(User, api_key.user_id)
    if user is None or not user.is_active:
        return None
    return user, api_key


async def touch_last_used(db: AsyncSession, api_key_id: UUID) -> None:
    try:
        api_key = await db.get(ApiKey, api_key_id)
        if api_key is not None:
            api_key.last_used_at = datetime.now(timezone.utc)
            await db.commit()
    except Exception:
        await db.rollback()
