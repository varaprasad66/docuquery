"""Background document processing: parse -> OCR fallback -> chunk -> embed -> store."""

import logging
import time
import uuid as uuidlib
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import Chunk, Document, DocumentStatus
from app.services import pdf as pdf_service
from app.services.chunker import chunk_text, estimate_tokens
from app.services.embeddings import embed_texts_sync
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

EMBED_BATCH_SIZE = 32


def _session_factory() -> sessionmaker[Session]:
    settings = get_settings()
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    return sessionmaker(engine, expire_on_commit=False)


def _set_progress(db: Session, doc_id, progress: float, **fields) -> None:
    doc = db.get(Document, uuidlib.UUID(str(doc_id)))
    if doc is None:
        return
    doc.progress = progress
    for key, value in fields.items():
        setattr(doc, key, value)
    db.commit()


@celery_app.task(name="process_document", bind=True, max_retries=2)
def process_document(self, document_id: str) -> None:
    settings = get_settings()
    started = time.time()
    db = _session_factory()()
    try:
        doc = db.get(Document, uuidlib.UUID(document_id))
        if doc is None:
            logger.error("Document %s not found", document_id)
            return

        pdf_path = Path(doc.stored_path)
        try:
            # 1. Parse pages (OCR fallback per page).  0 -> 40%
            pages = pdf_service.extract_pages(pdf_path)
            _set_progress(db, doc.id, 0.4, page_count=len(pages))
            logger.info("Doc %s: %d pages extracted", document_id, len(pages))

            # 2. Chunk.  40 -> 55%
            all_chunks: list[tuple[int, str]] = []
            for page in pages:
                for piece in chunk_text(
                    page.text, settings.chunk_size, settings.chunk_overlap
                ):
                    all_chunks.append((page.page, piece))
            _set_progress(db, doc.id, 0.55, chunk_count=len(all_chunks))

            if not all_chunks:
                doc.status = DocumentStatus.failed
                doc.error = "No extractable text found (scanned pages need Tesseract OCR)."
                db.commit()
                return

            # 3. Embed in batches.  55 -> 95%
            chunk_rows: list[Chunk] = []
            for batch_start in range(0, len(all_chunks), EMBED_BATCH_SIZE):
                batch = all_chunks[batch_start : batch_start + EMBED_BATCH_SIZE]
                try:
                    vectors = embed_texts_sync([content for _, content in batch])
                except Exception:
                    logger.exception("Embedding failed; storing chunks without vectors")
                    vectors = [None] * len(batch)  # type: ignore[list-item]
                for (page_no, content), vector in zip(batch, vectors):
                    chunk_rows.append(
                        Chunk(
                            document_id=doc.id,
                            page=page_no,
                            chunk_index=len(chunk_rows),
                            content=content,
                            token_estimate=estimate_tokens(content),
                            embedding=vector,
                        )
                    )
                _set_progress(
                    db, doc.id, 0.55 + 0.4 * (batch_start + len(batch)) / len(all_chunks)
                )

            # 4. Persist + finalize.  95 -> 100%
            for row in chunk_rows:
                db.add(row)
            doc.status = DocumentStatus.ready
            doc.progress = 1.0
            doc.error = None
            db.commit()
            logger.info(
                "Doc %s ready: %d chunks in %.1fs", document_id, len(chunk_rows), time.time() - started
            )
        except Exception as exc:
            db.rollback()
            doc = db.get(Document, uuidlib.UUID(document_id))
            if doc is not None:
                doc.status = DocumentStatus.failed
                doc.error = f"{type(exc).__name__}: {exc}"[:2000]
                db.commit()
            logger.exception("Processing failed for doc %s", document_id)
    finally:
        db.close()
