"""Structured extraction API (professional edition and up).

POST /api/extract           — synchronous extraction, returns the result inline.
POST /api/extract/jobs      — async mode for large documents: creates a job on
                              the existing worker path and returns immediately.
GET  /api/extract/jobs/{id} — job status + result.
GET  /api/extract/templates — the built-in templates and their schemas.
"""

import logging
import uuid as uuidlib

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import client_identity, get_current_user_optional_dep
from app.config import get_settings
from app.db.models import Chunk, Document, DocumentStatus, ExtractionJob, ExtractionJobStatus, User
from app.db.session import get_db
from app.editions import require_professional
from app.services.audit import record_audit
from app.services.extraction import (
    TEMPLATES,
    Excerpt,
    ExtractionError,
    resolve_schema,
    run_extraction_core,
)
from app.services.llm import llm_client
from app.services.security import ensure_document_read
from app.workers.tasks import process_extraction_job, process_extraction_job_core

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/extract", tags=["extract"], dependencies=[Depends(require_professional)])


class ExtractRequest(BaseModel):
    document_ids: list[uuidlib.UUID] = Field(min_length=1, max_length=10)
    template: str | None = Field(default=None, max_length=64)
    json_schema: dict | None = None
    async_mode: bool = False


class ExtractOut(BaseModel):
    data: dict
    confidence: str
    citations: list
    template: str


class ExtractJobOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuidlib.UUID
    status: ExtractionJobStatus
    template: str
    document_ids: list | None = None
    result: dict | None = None
    error: str | None = None
    confidence: str | None = None


async def _load_excerpts(
    db: AsyncSession,
    document_ids: list[uuidlib.UUID],
    user: User | None,
) -> list[Excerpt]:
    """Tenant-checked chunks per document, in reading order, capped per doc."""
    settings = get_settings()
    excerpts: list[Excerpt] = []
    for doc_id in document_ids:
        doc = await db.get(Document, doc_id)
        if doc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Document {doc_id} not found.")
        ensure_document_read(user, doc)
        if doc.status != DocumentStatus.ready:
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"Document {doc.original_name} is not ready."
            )
        result = await db.execute(
            select(Chunk)
            .where(Chunk.document_id == doc_id)
            .order_by(Chunk.chunk_index)
            .limit(settings.extract_max_chunks_per_doc)
        )
        for chunk in result.scalars():
            excerpts.append(
                Excerpt(
                    index=len(excerpts) + 1,
                    document_name=doc.original_name,
                    location=chunk.location or f"Page {chunk.page}",
                    content=chunk.content,
                )
            )
    return excerpts


async def _ask_json_async(messages: list[dict]) -> str:
    settings = get_settings()
    client = llm_client()
    response = await client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        temperature=0.0,
        max_tokens=settings.extract_max_tokens,
        stream=False,
    )
    return response.choices[0].message.content or ""


def _dispatch_extraction(job_id: str, background_tasks: BackgroundTasks) -> None:
    settings = get_settings()
    if settings.use_celery:
        try:
            process_extraction_job.delay(job_id)
            logger.info("Queued extraction job %s via Celery", job_id)
            return
        except Exception as exc:
            logger.warning("Celery broker unavailable (%s); using BackgroundTasks", exc)
    background_tasks.add_task(process_extraction_job_core, job_id)


@router.get("/templates")
async def list_templates():
    return {
        name: {"description": entry["description"], "schema": entry["schema"]}
        for name, entry in TEMPLATES.items()
    }


@router.post("", response_model=ExtractOut)
async def extract_sync(
    body: ExtractRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user_optional_dep),
):
    try:
        template, schema = resolve_schema(body.template, body.json_schema)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    excerpts = await _load_excerpts(db, body.document_ids, user)
    ip, ua = await client_identity(request)
    await record_audit(
        db, "extract.run", actor_id=user.id if user else None, resource_type="extraction",
        resource_id=";".join(str(d) for d in body.document_ids)[:64], ip=ip, user_agent=ua,
        detail={"template": template},
    )

    try:
        result = await run_extraction_core(schema, excerpts, _ask_json_async, template)
    except ExtractionError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return ExtractOut(
        data=result.data,
        confidence=result.confidence,
        citations=result.citations,
        template=result.template,
    )


@router.post("/jobs", response_model=ExtractJobOut, status_code=status.HTTP_201_CREATED)
async def create_extraction_job(
    body: ExtractRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user_optional_dep),
):
    # Validate the request fully before accepting the job (fail fast, 4xx).
    try:
        template, schema = resolve_schema(body.template, body.json_schema)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    await _load_excerpts(db, body.document_ids, user)

    job = ExtractionJob(
        owner_id=user.id if user else None,
        status=ExtractionJobStatus.pending,
        template=template,
        document_ids=[str(d) for d in body.document_ids],
        schema_json=schema,
    )
    db.add(job)
    ip, ua = await client_identity(request)
    await record_audit(
        db, "extract.job", actor_id=user.id if user else None, resource_type="extraction",
        resource_id=str(job.id), ip=ip, user_agent=ua, detail={"template": template},
    )
    await db.commit()
    await db.refresh(job)

    _dispatch_extraction(str(job.id), background_tasks)
    return job


@router.get("/jobs/{job_id}", response_model=ExtractJobOut)
async def get_extraction_job(
    job_id: uuidlib.UUID,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user_optional_dep),
):
    job = await db.get(ExtractionJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Extraction job not found.")
    # Same 404-shape access rule as other per-user surfaces.
    if user is not None and job.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Extraction job not found.")
    if user is None and job.owner_id is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Extraction job not found.")
    return job
