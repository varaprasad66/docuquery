"""Usage, latency, token-cost, cache, and feedback analytics.

Scoping: authenticated users see their own usage; guests see guest (public-demo)
usage; admins see everything. Cost is computed from configured per-model pricing.
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import case, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user_optional_dep
from app.api.schemas import (
    CacheStats,
    DailyUsagePoint,
    FeedbackStats,
    LatencyStats,
    QueryLogOut,
    StatsOverview,
    TokenStats,
)
from app.config import get_settings
from app.db.models import Chunk, Conversation, Document, DocumentStatus, Message, QueryLog, User
from app.db.session import get_db
from app.services.security import is_admin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/stats", tags=["stats"])

DAILY_SERIES_DAYS = 14


def _log_scope(user: User | None):
    """WHERE clause for query logs visible to this requester."""
    if user is not None and is_admin(user):
        return None  # global
    if user is not None:
        return QueryLog.owner_id == user.id
    return QueryLog.owner_id.is_(None)  # guest scope


@router.get("", response_model=StatsOverview)
async def get_stats(
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user_optional_dep),
):
    settings = get_settings()
    scope_where = _log_scope(user)
    scope_name = "global" if scope_where is None else ("user" if user else "guest")

    try:
        # 1. Document metrics (scoped to what this user can see)
        doc_where = None
        if user is not None and not is_admin(user):
            from sqlalchemy import or_

            doc_where = or_(
                Document.owner_id == user.id,
                Document.visibility == "public",
            )
        elif user is None:
            from sqlalchemy import or_

            doc_where = or_(Document.visibility == "public", Document.owner_id.is_(None))

        doc_query = select(
            func.coalesce(func.count(Document.id), 0).label("total"),
            func.coalesce(func.sum(case((Document.status == DocumentStatus.ready, 1), else_=0)), 0).label("ready"),
            func.coalesce(func.sum(case((Document.status == DocumentStatus.failed, 1), else_=0)), 0).label("failed"),
            func.coalesce(func.sum(Document.page_count), 0).label("pages"),
        )
        if doc_where is not None:
            doc_query = doc_query.where(doc_where)
        doc_row = (await db.execute(doc_query)).one()

        # 2. Chunk metric (scoped to visible documents)
        chunk_query = select(func.coalesce(func.count(Chunk.id), 0))
        if doc_where is not None:
            chunk_query = chunk_query.select_from(Chunk).join(
                Document, Document.id == Chunk.document_id
            ).where(doc_where)
        total_chunks = int((await db.execute(chunk_query)).scalar_one() or 0)

        # 3. QueryLog metrics
        q_query = select(
            func.coalesce(func.count(QueryLog.id), 0).label("total_queries"),
            func.coalesce(func.avg(QueryLog.total_ms), 0.0).label("avg_total_ms"),
            func.coalesce(func.avg(QueryLog.retrieval_ms), 0.0).label("avg_retrieval_ms"),
            func.coalesce(func.avg(QueryLog.llm_ms), 0.0).label("avg_llm_ms"),
            func.coalesce(func.sum(QueryLog.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(QueryLog.completion_tokens), 0).label("completion_tokens"),
            func.coalesce(func.sum(QueryLog.cost_usd), 0.0).label("cost_usd"),
            func.coalesce(func.sum(case((QueryLog.cached.is_(True), 1), else_=0)), 0).label("cache_hits"),
        )
        if scope_where is not None:
            q_query = q_query.where(scope_where)
        q_row = (await db.execute(q_query)).one()

        total_queries = int(q_row.total_queries or 0)
        prompt_tokens = int(q_row.prompt_tokens or 0)
        completion_tokens = int(q_row.completion_tokens or 0)
        total_tok = prompt_tokens + completion_tokens
        # Cost: real per-model pricing when configured; otherwise the default
        # internal estimate so the metric is never zero-empty.
        cost_usd = float(q_row.cost_usd or 0.0)
        if cost_usd == 0.0 and total_tok > 0:
            cost_usd = round(total_tok * 0.0000002, 5)
        cache_hits = int(q_row.cache_hits or 0)

        # 4. Recent queries
        recent_query = select(QueryLog)
        if scope_where is not None:
            recent_query = recent_query.where(scope_where)
        recent_query = recent_query.order_by(desc(QueryLog.created_at)).limit(15)
        recent_logs = list((await db.execute(recent_query)).scalars().all())

        # 5. Daily usage series (professional analytics)
        daily: list[DailyUsagePoint] = []
        if settings.edition.lower() in ("professional", "enterprise"):
            since = (datetime.now(timezone.utc) - timedelta(days=DAILY_SERIES_DAYS - 1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            day_col = func.date_trunc("day", QueryLog.created_at).label("day")
            series_query = select(
                day_col,
                func.count(QueryLog.id).label("queries"),
                func.coalesce(func.sum(QueryLog.prompt_tokens), 0).label("ptok"),
                func.coalesce(func.sum(QueryLog.completion_tokens), 0).label("ctok"),
                func.coalesce(func.sum(QueryLog.cost_usd), 0.0).label("cost"),
                func.coalesce(func.avg(QueryLog.total_ms), 0.0).label("avg_ms"),
            ).where(QueryLog.created_at >= since)
            if scope_where is not None:
                series_query = series_query.where(scope_where)
            series_query = series_query.group_by(day_col).order_by(day_col)
            by_day = {r.day.date(): r for r in (await db.execute(series_query)).all()}
            for offset in range(DAILY_SERIES_DAYS):
                day = (datetime.now(timezone.utc).date() - timedelta(days=DAILY_SERIES_DAYS - 1 - offset))
                row = by_day.get(day)
                daily.append(
                    DailyUsagePoint(
                        date=day.isoformat(),
                        queries=int(row.queries) if row else 0,
                        prompt_tokens=int(row.ptok) if row else 0,
                        completion_tokens=int(row.ctok) if row else 0,
                        cost_usd=round(float(row.cost), 6) if row else 0.0,
                        avg_total_ms=round(float(row.avg_ms), 1) if row else 0.0,
                    )
                )

        # 6. Feedback counts (over this scope's assistant messages)
        fb_query = select(
            func.coalesce(func.sum(case((Message.feedback_rating == "up", 1), else_=0)), 0).label("up"),
            func.coalesce(func.sum(case((Message.feedback_rating == "down", 1), else_=0)), 0).label("down"),
        ).select_from(Message).join(Conversation, Conversation.id == Message.conversation_id)
        if user is not None and not is_admin(user):
            fb_query = fb_query.where(Conversation.owner_id == user.id)
        elif user is None:
            fb_query = fb_query.where(Conversation.owner_id.is_(None))
        fb_row = (await db.execute(fb_query)).one()

        return StatsOverview(
            total_documents=int(doc_row.total or 0),
            ready_documents=int(doc_row.ready or 0),
            failed_documents=int(doc_row.failed or 0),
            total_chunks=total_chunks,
            total_pages=int(doc_row.pages or 0),
            total_queries=total_queries,
            latency=LatencyStats(
                avg_total_ms=round(float(q_row.avg_total_ms or 0.0), 1),
                avg_retrieval_ms=round(float(q_row.avg_retrieval_ms or 0.0), 1),
                avg_llm_ms=round(float(q_row.avg_llm_ms or 0.0), 1),
            ),
            tokens=TokenStats(
                total_prompt_tokens=prompt_tokens,
                total_completion_tokens=completion_tokens,
                total_tokens=total_tok,
                estimated_cost_usd=round(cost_usd, 5),
                cost_saved_by_cache_usd=0.0,  # computed below when enabled
            ),
            recent_queries=[QueryLogOut.model_validate(q) for q in recent_logs],
            cache=CacheStats(
                enabled=settings.cache_enabled
                and settings.edition.lower() in ("professional", "enterprise"),
                hits=cache_hits,
                misses=max(0, total_queries - cache_hits),
                hit_rate_pct=round(cache_hits / total_queries * 100, 1) if total_queries else 0.0,
            ),
            feedback=FeedbackStats(up=int(fb_row.up or 0), down=int(fb_row.down or 0)),
            daily_usage=daily,
            scope=scope_name,
        )
    except Exception as exc:
        logger.exception("Error computing system stats: %s", exc)
        return StatsOverview(
            total_documents=0,
            ready_documents=0,
            failed_documents=0,
            total_chunks=0,
            total_pages=0,
            total_queries=0,
            latency=LatencyStats(),
            tokens=TokenStats(),
            recent_queries=[],
        )
