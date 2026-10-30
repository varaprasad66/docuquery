"""API key management (JWT-authenticated; API keys cannot manage keys).

POST   /api/keys      — create (raw key returned exactly once)
GET    /api/keys      — list the caller's keys (prefix only, never the hash)
DELETE /api/keys/{id} — revoke
"""

import logging
import uuid as uuidlib
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import client_identity, require_jwt_session
from app.db.models import ApiKey, User
from app.db.session import get_db
from app.services.api_keys import generate_api_key, normalize_scopes
from app.services.audit import record_audit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/keys", tags=["api-keys"])


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] | None = None


class ApiKeyOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuidlib.UUID
    name: str
    prefix: str
    scopes: list | None
    revoked_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime


class ApiKeyCreated(ApiKeyOut):
    key: str  # raw key, shown exactly once


@router.post("", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    body: ApiKeyCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_jwt_session),
):
    try:
        scopes = normalize_scopes(body.scopes)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    raw, prefix, key_hash = generate_api_key()
    api_key = ApiKey(user_id=user.id, name=body.name, key_hash=key_hash, prefix=prefix, scopes=scopes)
    db.add(api_key)
    ip, ua = await client_identity(request)
    await record_audit(
        db, "apikey.create", actor_id=user.id, resource_type="api_key",
        resource_id=None, ip=ip, user_agent=ua,
        detail={"name": body.name, "scopes": scopes, "prefix": prefix},
    )
    await db.commit()
    await db.refresh(api_key)
    return ApiKeyCreated(
        id=api_key.id,
        name=api_key.name,
        prefix=api_key.prefix,
        scopes=api_key.scopes,
        revoked_at=api_key.revoked_at,
        last_used_at=api_key.last_used_at,
        created_at=api_key.created_at,
        key=raw,
    )


@router.get("", response_model=list[ApiKeyOut])
async def list_api_keys(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_jwt_session),
):
    result = await db.execute(
        select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.created_at.desc())
    )
    return list(result.scalars().all())


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    key_id: uuidlib.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_jwt_session),
):
    api_key = await db.get(ApiKey, key_id)
    if api_key is None or api_key.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API key not found.")
    if api_key.revoked_at is None:
        api_key.revoked_at = datetime.now(timezone.utc)
        ip, ua = await client_identity(request)
        await record_audit(
            db, "apikey.revoke", actor_id=user.id, resource_type="api_key",
            resource_id=str(api_key.id), ip=ip, user_agent=ua, detail={"prefix": api_key.prefix},
        )
        await db.commit()
