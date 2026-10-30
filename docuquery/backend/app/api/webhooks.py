"""Webhook subscription management (JWT-authenticated).

POST   /api/webhooks                     — subscribe (secret shown once)
GET    /api/webhooks                     — list subscriptions
DELETE /api/webhooks/{id}                — unsubscribe
GET    /api/webhooks/{id}/deliveries     — recent delivery log
"""

import logging
import uuid as uuidlib

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import client_identity, require_jwt_session
from app.db.models import User, Webhook, WebhookDelivery
from app.db.session import get_db
from app.services.audit import record_audit
from app.services.url_fetch import UrlImportError, assert_public_url
from app.services.webhooks import EVENTS, generate_secret

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


class WebhookCreate(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    events: list[str] = Field(min_length=1, max_length=len(EVENTS))

    @field_validator("events")
    @classmethod
    def known_events(cls, v: list[str]) -> list[str]:
        cleaned = []
        for event in v:
            if event not in EVENTS:
                raise ValueError(f"Unknown event '{event}'. Valid: {', '.join(EVENTS)}.")
            if event not in cleaned:
                cleaned.append(event)
        return cleaned


class WebhookOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuidlib.UUID
    url: str
    events: list | None
    is_active: bool
    created_at: object


class WebhookCreated(WebhookOut):
    secret: str  # shown exactly once


class WebhookDeliveryOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    event: str
    status: str
    attempts: int
    response_code: int | None
    error: str | None
    created_at: object


async def _owned_webhook(db: AsyncSession, webhook_id: uuidlib.UUID, user: User) -> Webhook:
    webhook = await db.get(Webhook, webhook_id)
    if webhook is None or webhook.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Webhook not found.")
    return webhook


@router.post("", response_model=WebhookCreated, status_code=status.HTTP_201_CREATED)
async def create_webhook(
    body: WebhookCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_jwt_session),
):
    try:
        assert_public_url(body.url)
    except UrlImportError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    secret = generate_secret()
    webhook = Webhook(user_id=user.id, url=body.url, secret=secret, events=body.events)
    db.add(webhook)
    ip, ua = await client_identity(request)
    await record_audit(
        db, "webhook.create", actor_id=user.id, resource_type="webhook",
        resource_id=str(webhook.id), ip=ip, user_agent=ua,
        detail={"url": body.url[:256], "events": body.events},
    )
    await db.commit()
    await db.refresh(webhook)
    return WebhookCreated(
        id=webhook.id, url=webhook.url, events=webhook.events,
        is_active=webhook.is_active, created_at=webhook.created_at, secret=secret,
    )


@router.get("", response_model=list[WebhookOut])
async def list_webhooks(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_jwt_session),
):
    result = await db.execute(
        select(Webhook).where(Webhook.user_id == user.id).order_by(Webhook.created_at.desc())
    )
    return list(result.scalars().all())


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(
    webhook_id: uuidlib.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_jwt_session),
):
    webhook = await _owned_webhook(db, webhook_id, user)
    ip, ua = await client_identity(request)
    await record_audit(
        db, "webhook.delete", actor_id=user.id, resource_type="webhook",
        resource_id=str(webhook.id), ip=ip, user_agent=ua, detail={"url": webhook.url[:256]},
    )
    await db.delete(webhook)
    await db.commit()


@router.get("/{webhook_id}/deliveries", response_model=list[WebhookDeliveryOut])
async def list_deliveries(
    webhook_id: uuidlib.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_jwt_session),
):
    webhook = await _owned_webhook(db, webhook_id, user)
    result = await db.execute(
        select(WebhookDelivery)
        .where(WebhookDelivery.webhook_id == webhook.id)
        .order_by(WebhookDelivery.created_at.desc())
        .limit(50)
    )
    return list(result.scalars().all())
