"""Outgoing webhooks for document lifecycle events.

Events: document.ready, document.failed. Deliveries are signed with
HMAC-SHA256 over "<timestamp>.<body>" (header X-DocuQuery-Signature: t=...,v1=...)
so receivers can verify authenticity and reject replays. Failed deliveries are
retried with backoff and every series is recorded in webhook_deliveries.
Target URLs are re-checked against the SSRF guard at dispatch time.
"""

import hashlib
import hmac
import json
import logging
import secrets
import time
from uuid import UUID

import httpx
from sqlalchemy import select

from app.config import get_settings
from app.db.models import Webhook, WebhookDelivery
from app.services.url_fetch import assert_public_url

logger = logging.getLogger(__name__)

EVENTS = ("document.ready", "document.failed")


def generate_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(24)


def sign(secret: str, body: bytes, timestamp: str | None = None) -> str:
    """Stripe-style signature: t=<unix>,v1=<hmac_sha256(f"{t}.{body}")>."""
    t = timestamp or str(int(time.time()))
    mac = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={t},v1={mac}"


def verify_signature(secret: str, body: bytes, header: str, tolerance_seconds: int = 300) -> bool:
    """Receiver-side verification helper (documented in INTEGRATIONS.md)."""
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        t, v1 = parts["t"], parts["v1"]
    except (ValueError, KeyError):
        return False
    if abs(time.time() - int(t)) > tolerance_seconds:
        return False
    expected = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, v1)


def _post(url: str, headers: dict, body: bytes, timeout: float) -> tuple[int | None, str | None]:
    """Single delivery attempt. Returns (http_status, error). Module-level so
    tests can patch it; kept sync — webhook delivery runs on the worker path."""
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            response = client.post(url, headers=headers, content=body)
            return response.status_code, None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _deliver_with_retries(webhook: Webhook, event: str, body: bytes) -> tuple[int | None, str | None, int]:
    """Deliver with SSRF re-check and retries+backoff. Returns (status, error, attempts)."""
    settings = get_settings()
    max_attempts = max(1, settings.webhook_max_attempts)
    status_code, error, attempts = None, None, 0
    for attempt in range(max_attempts):
        attempts = attempt + 1
        try:
            assert_public_url(webhook.url)  # a stored URL must still be public-safe
        except Exception as exc:
            return None, f"SSRF guard: {exc}", attempts
        status_code, error = _post(
            webhook.url,
            {
                "Content-Type": "application/json",
                "X-DocuQuery-Event": event,
                "X-DocuQuery-Signature": sign(webhook.secret, body),
            },
            body,
            settings.webhook_timeout_seconds,
        )
        if status_code is not None and 200 <= status_code < 300:
            return status_code, None, attempts
        if attempt + 1 < max_attempts:
            time.sleep(settings.webhook_retry_backoff_seconds)
    return status_code, error, attempts


def dispatch_event_sync(user_id: UUID | None, event: str, payload: dict) -> None:
    """Fire an event to all active webhooks of a user. Sync (worker path): sync
    session, sync HTTP with retries. Never raises — failures are recorded."""
    if event not in EVENTS:
        logger.warning("Ignoring unknown webhook event '%s'", event)
        return
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        settings = get_settings()
        engine = create_engine(settings.sync_database_url, pool_pre_ping=True)
        db = sessionmaker(engine, expire_on_commit=False)()
        try:
            body = json.dumps({"event": event, "data": payload}).encode()
            webhooks = (
                db.query(Webhook)
                .filter(Webhook.user_id == user_id, Webhook.is_active.is_(True))
                .all()
            )
            for webhook in webhooks:
                if event not in (webhook.events or []):
                    continue
                status_code, error, attempts = _deliver_with_retries(webhook, event, body)
                delivered = status_code is not None and 200 <= status_code < 300
                db.add(
                    WebhookDelivery(
                        webhook_id=webhook.id,
                        event=event,
                        payload=json.loads(body.decode()),
                        status="delivered" if delivered else "failed",
                        attempts=attempts,
                        response_code=status_code,
                        error=error[:2000] if error else None,
                    )
                )
            db.commit()
        finally:
            db.close()
            engine.dispose()
    except Exception:
        logger.exception("Webhook dispatch failed for event %s", event)


async def dispatch_event(db, user_id: UUID | None, event: str, payload: dict) -> None:
    """Async dispatch for API contexts; records one delivery row per webhook."""
    body = json.dumps({"event": event, "data": payload}).encode()
    result = await db.execute(
        select(Webhook).where(
            Webhook.user_id == user_id,
            Webhook.is_active.is_(True),
        )
    )
    webhooks = [w for w in result.scalars() if event in (w.events or [])]
    for webhook in webhooks:
        status_code, error, attempts = _deliver_with_retries(webhook, event, body)
        delivered = status_code is not None and 200 <= status_code < 300
        db.add(
            WebhookDelivery(
                webhook_id=webhook.id,
                event=event,
                payload=json.loads(body.decode()),
                status="delivered" if delivered else "failed",
                attempts=attempts,
                response_code=status_code,
                error=error[:2000] if error else None,
            )
        )
    await db.commit()
