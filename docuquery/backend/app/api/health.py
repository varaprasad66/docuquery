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
