import shutil
import uuid as uuidlib
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Document
from app.db.session import get_db
from app.api.schemas import DocumentDetail, DocumentOut
from app.workers.tasks import process_document

router = APIRouter(prefix="/api/documents", tags=["documents"])


def _pdf_magic(file: UploadFile) -> bool:
    header = file.file.read(5)
    file.file.seek(0)
    return header == b"%PDF-"


@router.post("", response_model=DocumentOut, status_code=201)
async def upload_document(file: UploadFile, db: AsyncSession = Depends(get_db)) -> Document:
    settings = get_settings()
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(422, "Only PDF files are accepted.")
    if not _pdf_magic(file):
        raise HTTPException(422, "File does not look like a valid PDF.")

    contents = await file.read()
    if len(contents) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"File exceeds {settings.max_upload_mb} MB limit.")

    doc_id = uuidlib.uuid4()
    stored_path = settings.upload_dir / f"{doc_id}.pdf"
    stored_path.write_bytes(contents)

    doc = Document(
        id=doc_id,
        original_name=file.filename,
        stored_path=str(stored_path),
        size_bytes=len(contents),
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    process_document.delay(str(doc_id))
    return doc


@router.get("", response_model=list[DocumentOut])
async def list_documents(db: AsyncSession = Depends(get_db)) -> list[Document]:
    result = await db.execute(select(Document).order_by(Document.created_at.desc()))
    return list(result.scalars().all())


@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(document_id: uuidlib.UUID, db: AsyncSession = Depends(get_db)):
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, "Document not found.")
    return doc


@router.delete("/{document_id}", status_code=204)
async def delete_document(document_id: uuidlib.UUID, db: AsyncSession = Depends(get_db)):
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, "Document not found.")
    stored = Path(doc.stored_path)
    await db.delete(doc)  # chunks cascade in Postgres
    await db.commit()
    stored.unlink(missing_ok=True)  # best-effort file cleanup
