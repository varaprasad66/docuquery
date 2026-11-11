import time

from fastapi import APIRouter
from sqlalchemy import text

from app.config import get_settings
from app.db.session import SessionLocal

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("")
async def health() -> dict:
    settings = get_settings()
    started = time.time()

    db_ok = True
    try:
        async with SessionLocal() as db:
            await db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - health endpoint must never raise
        db_ok = False

    llm_ok = True
    try:
        import openai

        client = openai.AsyncOpenAI(
            base_url=settings.llm_base_url, api_key=settings.llm_api_key, timeout=3
        )
        await client.models.list()
    except Exception:  # noqa: BLE001
        llm_ok = False

    return {
        "status": "ok" if (db_ok and llm_ok) else "degraded",
        "components": {"database": "ok" if db_ok else "down", "llm": "ok" if llm_ok else "down"},
        "model": settings.llm_model,
        "edition": settings.edition,
        "environment": settings.environment,
        "latency_ms": round((time.time() - started) * 1000),
    }


@router.get("/live")
async def liveness():
    return {"status": "ok"}


@router.get("/ready")
async def readiness():
    from fastapi.responses import JSONResponse
    from redis.asyncio import Redis
    import asyncio
    components = {}
    try:
        async with SessionLocal() as db:
            await asyncio.wait_for(db.execute(text("SELECT 1")), timeout=3)
        components["database"] = "ok"
    except Exception:
        components["database"] = "down"
    if get_settings().use_celery:
        try:
            async with Redis.from_url(get_settings().redis_url, socket_timeout=3, socket_connect_timeout=3) as redis:
                await redis.ping()
            components["broker"] = "ok"
        except Exception:
            components["broker"] = "down"
    ready = all(value == "ok" for value in components.values())
    return JSONResponse({"status": "ready" if ready else "unavailable", "components": components}, status_code=200 if ready else 503)


@router.get("/config")
async def public_configuration():
    s = get_settings()
    return {"max_upload_mb": s.max_upload_mb, "url_import_enabled": s.url_import_enabled,
            "registration_enabled": s.registration_enabled, "integrations_enabled": s.integrations_enabled}
