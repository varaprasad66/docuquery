import logging
from pathlib import Path
import shutil
import uuid as uuidlib

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user_optional, rate_limit_upload
from app.api.schemas import DocumentDetail, DocumentOut
from app.config import get_settings
from app.db.models import Chunk, Document, DocumentStatus, User
from app.db.session import get_db
from app.workers.tasks import process_document, process_document_core

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/documents", tags=["documents"])


def _pdf_magic(file: UploadFile) -> bool:
    try:
        header = file.file.read(5)
        file.file.seek(0)
        return header == b"%PDF-"
    except Exception:
        return False


def _dispatch_processing(document_id: str, background_tasks: BackgroundTasks) -> None:
    """Dispatch document processing: uses BackgroundTasks by default to avoid hung queues without Celery."""
    settings = get_settings()
    if settings.use_celery:
        try:
            process_document.delay(document_id)
            logger.info("Queued document %s via Celery", document_id)
            return
        except Exception as exc:
            logger.warning(
                "Celery broker unavailable (%s); falling back to FastAPI BackgroundTasks", exc
            )

    background_tasks.add_task(process_document_core, document_id)
    logger.info("Dispatched document %s to FastAPI BackgroundTasks", document_id)


@router.post("", response_model=DocumentOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(rate_limit_upload)])
async def upload_document(
    file: UploadFile,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
) -> Document:
    settings = get_settings()
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Only PDF files are accepted.")
    if not _pdf_magic(file):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "File does not look like a valid PDF.")

    contents = await file.read()
    if len(contents) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File exceeds {settings.max_upload_mb} MB limit.",
        )

    doc_id = uuidlib.uuid4()
    stored_path = settings.upload_dir / f"{doc_id}.pdf"
    stored_path.write_bytes(contents)

    doc = Document(
        id=doc_id,
        original_name=file.filename,
        stored_path=str(stored_path),
        size_bytes=len(contents),
        owner_id=user.id if user else None,
        status=DocumentStatus.processing,
        progress=0.01,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    _dispatch_processing(str(doc_id), background_tasks)
    return doc


@router.get("", response_model=list[DocumentOut])
async def list_documents(
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
) -> list[Document]:
    query = select(Document).order_by(Document.created_at.desc())
    if user:
        # Multi-tenant: user's docs + public unowned docs
        query = query.where((Document.owner_id == user.id) | (Document.owner_id.is_(None)))
    result = await db.execute(query)
    return list(result.scalars().all())


@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(
    document_id: uuidlib.UUID,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.")
    if user and doc.owner_id and doc.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied to this document.")
    return doc


@router.post("/{document_id}/retry", response_model=DocumentOut)
async def retry_document(
    document_id: uuidlib.UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    """Re-trigger extraction and indexing for a failed or stuck document."""
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.")
    if user and doc.owner_id and doc.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied to this document.")

    # Reset doc state
    doc.status = DocumentStatus.processing
    doc.error = None
    doc.progress = 0.05
    await db.commit()
    await db.refresh(doc)

    _dispatch_processing(str(doc.id), background_tasks)
    return doc


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuidlib.UUID,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.")
    if user and doc.owner_id and doc.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied to this document.")

    stored = Path(doc.stored_path)
    await db.delete(doc)  # chunks cascade in Postgres
    await db.commit()
    stored.unlink(missing_ok=True)  # best-effort file cleanup
