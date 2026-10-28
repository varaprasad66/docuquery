import time

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import SearchRequest, SearchResponse, SearchResult
from app.db.session import get_db
from app.services.retrieval import retrieve

router = APIRouter(prefix="/api/search", tags=["search"])


@router.post("", response_model=SearchResponse)
async def search(body: SearchRequest, db: AsyncSession = Depends(get_db)) -> SearchResponse:
    started = time.perf_counter()
    sources, methods = await retrieve(db, body.query, top_k=body.top_k)
    took_ms = round((time.perf_counter() - started) * 1000)
    return SearchResponse(
        query=body.query,
        methods=methods,
        results=[
            SearchResult(
                chunk_id=s.chunk_id,
                document_id=s.document_id,
                document_name=s.document_name,
                page=s.page,
                snippet=s.snippet,
                score=round(s.score, 6),
                matched_by=s.matched_by,
            )
            for s in sources
        ],
        took_ms=took_ms,
    )
