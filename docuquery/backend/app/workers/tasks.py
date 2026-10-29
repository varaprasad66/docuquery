"""Background document processing: parse -> OCR fallback -> chunk -> embed -> store."""

import logging
from pathlib import Path
import time
import uuid as uuidlib

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import Chunk, Document, DocumentStatus
from app.services import pdf as pdf_service
from app.services.chunker import chunk_text, estimate_tokens
from app.services.embeddings import embed_texts_sync
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# Smaller batch size for reliable Ollama throughput and smooth progress bar updates
EMBED_BATCH_SIZE = 10


def _session_factory() -> sessionmaker[Session]:
    settings = get_settings()
    engine = create_engine(settings.sync_database_url, pool_pre_ping=True)
    return sessionmaker(engine, expire_on_commit=False)


def _set_progress(db: Session, doc_id, progress: float, **fields) -> None:
    try:
        doc = db.get(Document, uuidlib.UUID(str(doc_id)))
        if doc is None:
            return
        doc.progress = round(progress, 3)
        for key, value in fields.items():
            setattr(doc, key, value)
        db.commit()
    except Exception as exc:
        logger.warning("Failed to update progress for doc %s: %s", doc_id, exc)
        db.rollback()


def process_document_core(document_id: str) -> None:
    """Core extraction, chunking, and embedding logic.

    Can be invoked directly via Celery worker OR as a FastAPI BackgroundTask.
    """
    settings = get_settings()
    started = time.time()
    db = _session_factory()()

    try:
        doc = db.get(Document, uuidlib.UUID(document_id))
        if doc is None:
            logger.error("Document %s not found", document_id)
            return

        pdf_path = Path(doc.stored_path)
        if not pdf_path.exists():
            doc.status = DocumentStatus.failed
            doc.error = f"File not found on disk at {pdf_path}"
            db.commit()
            return

        doc.status = DocumentStatus.processing
        doc.progress = 0.05
        db.commit()

        # 1. Parse pages (with live progress callback: 0.05 -> 0.35)
        def on_page_progress(current_page: int, total_pages: int):
            if total_pages > 0:
                pct = 0.05 + 0.30 * (current_page / total_pages)
                _set_progress(db, doc.id, pct, page_count=total_pages)

        pages = pdf_service.extract_pages(pdf_path, progress_cb=on_page_progress)
        _set_progress(db, doc.id, 0.35, page_count=len(pages))
        logger.info("Doc %s: %d pages extracted in %.1fs", document_id, len(pages), time.time() - started)

        # 2. Chunk text (0.35 -> 0.45)
        all_chunks: list[tuple[int, str]] = []
        for page in pages:
            if not page.text.strip():
                continue
            for piece in chunk_text(
                page.text, settings.chunk_size, settings.chunk_overlap
            ):
                all_chunks.append((page.page, piece))

        _set_progress(db, doc.id, 0.45, chunk_count=len(all_chunks))
        logger.info("Doc %s: %d total chunks generated", document_id, len(all_chunks))

        if not all_chunks:
            doc.status = DocumentStatus.failed
            doc.error = "No extractable text found in this PDF (scanned pages need Tesseract OCR)."
            db.commit()
            return

        # 3. Embed in batches (0.45 -> 0.95)
        chunk_rows: list[Chunk] = []
        total_batches = (len(all_chunks) + EMBED_BATCH_SIZE - 1) // EMBED_BATCH_SIZE
        embeddings_service_available = True

        for batch_idx, batch_start in enumerate(range(0, len(all_chunks), EMBED_BATCH_SIZE)):
            batch = all_chunks[batch_start : batch_start + EMBED_BATCH_SIZE]
            batch_texts = [content for _, content in batch]

            vectors = None
            if embeddings_service_available:
                try:
                    vectors = embed_texts_sync(batch_texts)
                except Exception as embed_exc:
                    logger.warning(
                        "Doc %s: Embedding batch %d/%d failed (%s); indexing remaining chunks without vectors for BM25",
                        document_id,
                        batch_idx + 1,
                        total_batches,
                        embed_exc,
                    )
                    embeddings_service_available = False
                    vectors = [None] * len(batch)
            else:
                vectors = [None] * len(batch)

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

            progress_val = 0.45 + 0.50 * ((batch_start + len(batch)) / len(all_chunks))
            _set_progress(db, doc.id, min(progress_val, 0.95))

        # 4. Persist chunks & finalize (0.95 -> 1.0)
        for row in chunk_rows:
            db.add(row)

        doc = db.get(Document, uuidlib.UUID(document_id))
        if doc is not None:
            doc.status = DocumentStatus.ready
            doc.progress = 1.0
            doc.chunk_count = len(chunk_rows)
            doc.error = None
            db.commit()

        logger.info(
            "Doc %s ready: %d chunks across %d pages processed in %.1fs",
            document_id,
            len(chunk_rows),
            len(pages),
            time.time() - started,
        )

    except Exception as exc:
        logger.exception("Processing failed for doc %s", document_id)
        try:
            db.rollback()
            doc = db.get(Document, uuidlib.UUID(document_id))
            if doc is not None:
                doc.status = DocumentStatus.failed
                doc.error = f"{type(exc).__name__}: {exc}"[:2000]
                db.commit()
        except Exception as inner_exc:
            logger.error("Failed to update failed status in DB: %s", inner_exc)
    finally:
        db.close()


@celery_app.task(name="process_document", bind=True, max_retries=1)
def process_document(self, document_id: str) -> None:
    process_document_core(document_id)
