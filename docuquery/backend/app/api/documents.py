import logging
import asyncio
import uuid as uuidlib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, UploadFile, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    client_identity,
    get_current_user_optional_dep,
    get_current_user,
    rate_limit_upload,
    require_api_key_scope,
)
from app.api.schemas import DocumentDetail, DocumentOut, UrlImportRequest, VisibilityUpdate
from app.config import get_settings
from app.db.models import Document, DocumentStatus, DocumentVisibility, User, SemanticCacheEntry, StorageCleanup
from app.db.session import get_db
from app.services.audit import record_audit
from app.services import storage
from app.services.parsers import (
    SUPPORTED_EXTENSIONS,
    check_zip_safety,
    sniff_kind_bytes,
    stored_extension_for_kind,
    validate_content_kind,
)
from app.services.security import ensure_document_mutate, ensure_document_read
from app.services.scan import scan_bytes
from app.services.url_fetch import UrlImportError, assert_public_url, fetch_document, suggest_name
from app.workers.tasks import process_document, process_document_core

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/documents", tags=["documents"])

_ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


def _pdf_magic(file: UploadFile) -> bool:
    try:
        header = file.file.read(5)
        file.file.seek(0)
        return header == b"%PDF-"
    except Exception:
        return False


def _magic_matches(file: UploadFile, ext: str) -> bool:
    """First-line content validation before the file is even stored.

    Office formats are ZIP containers (PK signature); PDF has %PDF- magic.
    Text formats have no useful magic — their content kind is validated below
    and again inside the parser.
    """
    try:
        header = file.file.read(8)
        file.file.seek(0)
    except Exception:
        return False
    if ext == ".pdf":
        return header.startswith(b"%PDF-")
    if ext in (".docx", ".pptx", ".xlsx"):
        return header[:4] in _ZIP_SIGNATURES
    return True


def _dispatch_processing(document_id: str, background_tasks: BackgroundTasks) -> None:
    """Dispatch document processing: uses BackgroundTasks by default to avoid hung queues without Celery."""
    settings = get_settings()
    if settings.use_celery:
        try:
            process_document.delay(document_id)
            logger.info("Queued document %s via Celery", document_id)
            return
        except Exception:
            logger.warning("Broker unavailable; document remains queued for recovery", exc_info=True)
            if settings.is_production:
                return  # durable processing row is redispatched by maintenance


    background_tasks.add_task(process_document_core, document_id)
    logger.info("Dispatched document %s to FastAPI BackgroundTasks", document_id)


async def _store_upload(db, user, doc_id, name, contents, mime_type, visibility, source_url=None):
    """Reserve quota and an object compensation record before uploading bytes."""
    settings = get_settings()
    await db.execute(select(User.id).where(User.id == user.id).with_for_update())
    if settings.max_uploads_per_day > 0:
        midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        count = await db.scalar(select(func.count(Document.id)).where(Document.owner_id == user.id, Document.created_at >= midnight))
        if count >= settings.max_uploads_per_day:
            raise HTTPException(429, "Daily upload quota reached.")
    location = storage.locator(user.id, doc_id, Path(name).suffix.lower())
    doc = Document(id=doc_id, original_name=name, stored_path=location, mime_type=mime_type,
                   source_url=source_url, size_bytes=len(contents), owner_id=user.id,
                   visibility=visibility, status=DocumentStatus.processing, progress=0.01)
    cleanup = StorageCleanup(locator=location)
    db.add_all([doc, cleanup])
    await db.commit()  # Atomic quota reservation before the object write.
    try:
        await asyncio.to_thread(storage.put, location, contents)
    except Exception:
        logger.exception("Source upload failed for document %s", doc_id)
        # No referenced source remains; the durable compensation record can be retried.
        await db.delete(doc)
        await db.commit()
        raise HTTPException(503, "Document storage is temporarily unavailable. Please retry.") from None
    await db.delete(cleanup)
    await db.commit()
    return doc


@router.post("", response_model=DocumentOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(rate_limit_upload), Depends(require_api_key_scope("upload"))])
async def upload_document(
    file: UploadFile,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user),
    visibility: str = "private",
) -> Document:
    settings = get_settings()
    ext = Path(file.filename or "").suffix.lower()
    if not file.filename or ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Unsupported file type. Accepted: {', '.join(sorted(SUPPORTED_EXTENSIONS))}.",
        )
    if not _magic_matches(file, ext):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"File does not look like a valid {ext[1:].upper()} document.",
        )

    contents = await file.read()
    if len(contents) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File exceeds {settings.max_upload_mb} MB limit.",
        )
    if not contents:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "File is empty.")

    # Content-based validation: zip bombs for OOXML, content kind for the rest.
    try:
        if ext in (".docx", ".pptx", ".xlsx"):
            check_zip_safety(contents)
        validate_content_kind(contents, ext)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    # Optional malware scan (ClamAV). Infected files are quarantined on disk
    # for forensics, never ingested.
    clean, threat = scan_bytes(contents)
    if not clean:
        quarantine_dir = settings.upload_dir / "quarantine"
        try:
            quarantine_dir.mkdir(parents=True, exist_ok=True)
            (quarantine_dir / f"{uuidlib.uuid4().hex}.bin").write_bytes(contents)
        except Exception:
            logger.warning("Failed to persist quarantined upload", exc_info=True)
        ip, ua = await client_identity(request)
        await record_audit(
            db, "doc.quarantined", actor_id=user.id if user else None, resource_type="document",
            resource_id=None, ip=ip, user_agent=ua,
            detail={"threat": threat, "name": file.filename[:256]},
        )
        await db.commit()
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "File failed the malware scan and was not accepted.",
        )

    # Upload quota (professional edition setting; 0 = unlimited).
    if user and settings.max_uploads_per_day > 0:
        midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        count_res = await db.execute(
            select(func.count(Document.id)).where(
                Document.owner_id == user.id, Document.created_at >= midnight
            )
        )
        if int(count_res.scalar_one() or 0) >= settings.max_uploads_per_day:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                f"Daily upload quota reached ({settings.max_uploads_per_day}/day).",
            )

    # Guests always upload private; visibility is owner's choice otherwise.
    requested_visibility = (
        DocumentVisibility.public.value if visibility == "public" else DocumentVisibility.private.value
    )
    if user is None:
        requested_visibility = DocumentVisibility.private.value

    doc_id = uuidlib.uuid4()
    doc = await _store_upload(db, user, doc_id, file.filename, contents,
                              file.content_type or "application/octet-stream", requested_visibility)
    ip, ua = await client_identity(request)
    await record_audit(
        db, "doc.upload", actor_id=user.id if user else None, resource_type="document",
        resource_id=str(doc_id), ip=ip, user_agent=ua, detail={"name": file.filename[:256]},
    )
    await db.commit()
    await db.refresh(doc)

    _dispatch_processing(str(doc_id), background_tasks)
    return doc


@router.post("/url", response_model=DocumentOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(rate_limit_upload), Depends(require_api_key_scope("upload"))])
async def import_document_from_url(
    body: UrlImportRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user),
) -> Document:
    """Import a document by URL: SSRF-guarded fetch, size cap, content-type check."""
    settings = get_settings()
    if not settings.url_import_enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "URL import is disabled on this deployment.")

    try:
        assert_public_url(body.url)
        contents, content_type = fetch_document(
            body.url,
            max_bytes=settings.max_upload_mb * 1024 * 1024,
            timeout_seconds=settings.url_fetch_timeout_seconds,
        )
    except UrlImportError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except httpx.TransportError as exc:
        logger.info("URL import fetch failed for %s: %s", body.url, exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Could not download the document from that URL."
        ) from exc
    if not contents:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The remote document is empty.")

    url_ext = Path(urlparse(body.url).path).suffix.lower()
    hint_ext = url_ext if url_ext in SUPPORTED_EXTENSIONS else None
    try:
        kind = sniff_kind_bytes(contents)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    ext = stored_extension_for_kind(kind, hint_ext)
    if ext is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Could not determine a supported document format at that URL.",
        )
    if ext in (".docx", ".pptx", ".xlsx"):
        try:
            check_zip_safety(contents)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    # Guests always upload private; visibility is owner's choice otherwise.
    requested_visibility = (
        DocumentVisibility.public.value if body.visibility == "public" else DocumentVisibility.private.value
    )
    if user is None:
        requested_visibility = DocumentVisibility.private.value

    doc_id = uuidlib.uuid4()
    doc = await _store_upload(db, user, doc_id, suggest_name(body.url, ext), contents,
                              content_type, requested_visibility, body.url)
    ip, ua = await client_identity(request)
    await record_audit(
        db, "doc.url_import", actor_id=user.id if user else None, resource_type="document",
        resource_id=str(doc_id), ip=ip, user_agent=ua, detail={"url": body.url[:512]},
    )
    await db.commit()
    await db.refresh(doc)

    _dispatch_processing(str(doc_id), background_tasks)
    return doc


@router.get("", response_model=list[DocumentOut], dependencies=[Depends(require_api_key_scope("read"))])
async def list_documents(
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user_optional_dep),
) -> list[Document]:
    if not get_settings().public_document_sharing_enabled:
        if user is None:
            return []
        query = select(Document).where(Document.owner_id == user.id)
    elif user is not None:
        # Own documents (any visibility) + everything public.
        query = select(Document).where(
            (Document.owner_id == user.id) | (Document.visibility == DocumentVisibility.public.value)
        )
    else:
        # Anonymous visitors see only explicitly public sample documents.
        query = select(Document).where(
            Document.visibility == DocumentVisibility.public.value
        )
    query = query.order_by(Document.created_at.desc())
    result = await db.execute(query)
    return list(result.scalars().all())


@router.get("/{document_id}", response_model=DocumentDetail, dependencies=[Depends(require_api_key_scope("read"))])
async def get_document(
    document_id: uuidlib.UUID,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user_optional_dep),
):
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.")
    ensure_document_read(user, doc)
    return doc


@router.patch("/{document_id}/visibility", response_model=DocumentOut, dependencies=[Depends(require_api_key_scope("upload"))])
async def update_visibility(
    document_id: uuidlib.UUID,
    body: VisibilityUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.")
    ensure_document_mutate(user, doc)
    await db.execute(delete(SemanticCacheEntry))
    doc.visibility = body.visibility
    ip, ua = await client_identity(request)
    await record_audit(
        db, "doc.visibility", actor_id=user.id if user else None, resource_type="document",
        resource_id=str(doc.id), ip=ip, user_agent=ua, detail={"visibility": body.visibility},
    )
    await db.commit()
    await db.refresh(doc)
    return doc


@router.post("/{document_id}/retry", response_model=DocumentOut, dependencies=[Depends(require_api_key_scope("upload"))])
async def retry_document(
    document_id: uuidlib.UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    """Re-trigger extraction and indexing for a failed or stuck document."""
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.")
    ensure_document_mutate(user, doc)

    doc.status = DocumentStatus.processing
    doc.error = None
    doc.progress = 0.05
    ip, ua = await client_identity(request)
    await record_audit(
        db, "doc.retry", actor_id=user.id if user else None, resource_type="document",
        resource_id=str(doc.id), ip=ip, user_agent=ua,
    )
    await db.commit()
    await db.refresh(doc)

    _dispatch_processing(str(doc.id), background_tasks)
    return doc


@router.post("/{document_id}/reembed", response_model=DocumentOut, dependencies=[Depends(require_api_key_scope("upload"))])
async def reembed_document(
    document_id: uuidlib.UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    """Re-embed a degraded (keyword-only) document, or one indexed with an old
    embedding configuration. Chunks are re-embedded in place — no re-parsing."""
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.")
    ensure_document_mutate(user, doc)
    if doc.status != DocumentStatus.ready:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Only processed documents can be re-embedded. Retry processing first.",
        )
    if doc.chunk_count == 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "Document has no indexed chunks.")

    doc.status = DocumentStatus.processing
    doc.progress = 0.05
    ip, ua = await client_identity(request)
    await record_audit(
        db, "doc.reembed", actor_id=user.id if user else None, resource_type="document",
        resource_id=str(doc.id), ip=ip, user_agent=ua,
    )
    await db.commit()
    await db.refresh(doc)

    from app.workers.tasks import reembed_document as reembed_task, reembed_document_core

    if get_settings().use_celery:
        try:
            reembed_task.delay(str(doc.id))
        except Exception:
            logger.warning("Broker unavailable; re-embedding inline", exc_info=True)
            await asyncio.to_thread(reembed_document_core, str(doc.id))
    else:
        background_tasks.add_task(reembed_document_core, str(doc.id))
    return doc


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_api_key_scope("upload"))])
async def delete_document(
    document_id: uuidlib.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.")
    ensure_document_mutate(user, doc)

    db.add(StorageCleanup(locator=doc.stored_path))
    ip, ua = await client_identity(request)
    await record_audit(
        db, "doc.delete", actor_id=user.id if user else None, resource_type="document",
        resource_id=str(doc.id), ip=ip, user_agent=ua, detail={"name": doc.original_name[:256]},
    )
    await db.execute(delete(SemanticCacheEntry))
    await db.delete(doc)  # chunks cascade in Postgres
    await db.commit()
    # A durable cleanup row is consumed by the maintenance worker.


@router.get("/{document_id}/download", dependencies=[Depends(require_api_key_scope("read"))])
async def download_document(document_id: uuidlib.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    from fastapi.responses import Response
    from urllib.parse import quote
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, "Document not found.")
    ensure_document_read(user, doc)
    def read_source():
        with storage.materialize(doc.stored_path) as source:
            return source.read_bytes()
    content = await asyncio.to_thread(read_source)
    return Response(content, media_type="application/octet-stream", headers={
        "Content-Disposition": "attachment; filename*=UTF-8''" + quote(doc.original_name, safe=""),
        "Cache-Control": "private, no-store",
    })
