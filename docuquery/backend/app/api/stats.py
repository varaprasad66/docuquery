"""System usage, latency, and token cost analytics."""

import logging
from fastapi import APIRouter, Depends
from sqlalchemy import case, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import LatencyStats, QueryLogOut, StatsOverview, TokenStats
from app.db.models import Chunk, Document, DocumentStatus, QueryLog
from app.db.session import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("", response_model=StatsOverview)
async def get_stats(db: AsyncSession = Depends(get_db)):
    try:
        # 1. Document metrics
        doc_res = await db.execute(
            select(
                func.coalesce(func.count(Document.id), 0).label("total"),
                func.coalesce(func.sum(case((Document.status == DocumentStatus.ready, 1), else_=0)), 0).label("ready"),
                func.coalesce(func.sum(case((Document.status == DocumentStatus.failed, 1), else_=0)), 0).label("failed"),
                func.coalesce(func.sum(Document.page_count), 0).label("pages"),
            )
        )
        doc_row = doc_res.one()

        # 2. Chunk metric
        chunk_res = await db.execute(select(func.coalesce(func.count(Chunk.id), 0)))
        total_chunks = int(chunk_res.scalar_one() or 0)

        # 3. QueryLog metrics
        query_stats_res = await db.execute(
            select(
                func.coalesce(func.count(QueryLog.id), 0).label("total_queries"),
                func.coalesce(func.avg(QueryLog.total_ms), 0.0).label("avg_total_ms"),
                func.coalesce(func.avg(QueryLog.retrieval_ms), 0.0).label("avg_retrieval_ms"),
                func.coalesce(func.avg(QueryLog.llm_ms), 0.0).label("avg_llm_ms"),
                func.coalesce(func.sum(QueryLog.prompt_tokens), 0).label("prompt_tokens"),
                func.coalesce(func.sum(QueryLog.completion_tokens), 0).label("completion_tokens"),
            )
        )
        q_row = query_stats_res.one()

        total_queries = int(q_row.total_queries or 0)
        prompt_tokens = int(q_row.prompt_tokens or 0)
        completion_tokens = int(q_row.completion_tokens or 0)
        total_tok = prompt_tokens + completion_tokens
        cost_usd = round(total_tok * 0.0000002, 5)

        # 4. Recent queries
        recent_res = await db.execute(
            select(QueryLog).order_by(desc(QueryLog.created_at)).limit(15)
        )
        recent_logs = list(recent_res.scalars().all())

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
                estimated_cost_usd=cost_usd,
            ),
            recent_queries=[QueryLogOut.model_validate(q) for q in recent_logs],
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
