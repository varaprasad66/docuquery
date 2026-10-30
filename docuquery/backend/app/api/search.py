import time

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user_optional_dep, rate_limit_search
from app.api.schemas import SearchRequest, SearchResponse, SearchResult
from app.db.models import User
from app.db.session import get_db
from app.services.retrieval import retrieve

router = APIRouter(prefix="/api/search", tags=["search"])


@router.post("", response_model=SearchResponse, dependencies=[Depends(rate_limit_search)])
async def search(
    body: SearchRequest,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user_optional_dep),
) -> SearchResponse:
    started = time.perf_counter()
    owner_id = user.id if user else None
    sources, methods = await retrieve(
        db, body.query, top_k=body.top_k, owner_id=owner_id, document_ids=body.document_ids
    )
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
                location=s.location,
                snippet=s.snippet,
                score=round(s.score, 6),
                matched_by=s.matched_by,
            )
            for s in sources
        ],
        took_ms=took_ms,
    )
