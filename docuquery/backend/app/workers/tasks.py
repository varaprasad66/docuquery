"""Background document processing: parse (any supported format) -> OCR fallback
for PDFs -> chunk -> embed -> store."""

import logging
import os
from functools import lru_cache
from contextlib import ExitStack
import time
import uuid as uuidlib
from pathlib import Path

from sqlalchemy import create_engine, delete, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import Chunk, Document, DocumentStatus, ExtractionJob, ExtractionJobStatus
from app.services import parsers
from app.services.chunker import chunk_text, estimate_tokens
from app.services.embeddings import contextual_embed_input, embed_texts_sync, embedding_version
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# Smaller batch size for reliable Ollama throughput and smooth progress bar updates
EMBED_BATCH_SIZE = 10


@lru_cache(maxsize=4)
def _process_session_factory(pid: int) -> sessionmaker[Session]:
    settings = get_settings()
    engine = create_engine(settings.sync_database_url, pool_pre_ping=True, pool_size=settings.db_pool_size, max_overflow=settings.db_max_overflow, pool_timeout=settings.db_pool_timeout)
    return sessionmaker(engine, expire_on_commit=False)


def _session_factory() -> sessionmaker[Session]:
    return _process_session_factory(os.getpid())


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

    resources = ExitStack()
    lock_connection = None
    lock_id = uuidlib.UUID(document_id).int % (2**63 - 1)
    try:
        # A dedicated connection holds the session lock across progress commits.
        # PostgreSQL releases it on worker death, allowing redelivery to recover.
        lock_connection = db.get_bind().connect()
        acquired = lock_connection.execute(text("SELECT pg_try_advisory_lock(:id)"), {"id": lock_id}).scalar()
        if not acquired:
            return
        doc = db.get(Document, uuidlib.UUID(document_id))
        if doc is None:
            logger.error("Document %s not found", document_id)
            return

        from app.services.storage import materialize
        pdf_path = resources.enter_context(materialize(doc.stored_path))
        if not pdf_path.exists():
            doc.status = DocumentStatus.failed
            doc.error = "The source file is unavailable. Please upload it again."
            db.commit()
            return

        doc.status = DocumentStatus.processing
        doc.progress = 0.05
        db.commit()

        # Resolve the parser by extension; fall back to content sniffing for
        # legacy rows whose original_name carries no usable suffix.
        ext = Path(doc.original_name).suffix.lower()
        if ext not in parsers.PARSERS:
            kind = parsers.sniff_kind_bytes(pdf_path.read_bytes())
            ext = parsers.stored_extension_for_kind(kind) or ""
        if ext not in parsers.PARSERS:
            doc.status = DocumentStatus.failed
            doc.error = "Unsupported file type."
            db.commit()
            return

        # 1. Parse pages/sections (PDF path streams live progress: 0.05 -> 0.35)
        def on_page_progress(current_page: int, total_pages: int):
            if total_pages > 0:
                pct = 0.05 + 0.30 * (current_page / total_pages)
                _set_progress(db, doc.id, pct, page_count=total_pages)

        if ext == ".pdf" and settings.pdf_extractor == "markdown":
            # Layout-aware extraction: headings/tables survive as structure.
            sections = parsers.parse_pdf_markdown(pdf_path, progress_cb=on_page_progress)
        else:
            sections = parsers.parse_document(pdf_path, ext, progress_cb=on_page_progress)
        _set_progress(db, doc.id, 0.35, page_count=len(sections))
        logger.info("Doc %s: %d sections extracted in %.1fs", document_id, len(sections), time.time() - started)

        # 2. Chunk text (0.35 -> 0.45)
        all_chunks: list[tuple[int, str, str]] = []
        for section in sections:
            if not section.text.strip():
                continue
            for piece in chunk_text(
                section.text, settings.chunk_size, settings.chunk_overlap
            ):
                all_chunks.append((section.index, piece, section.location))

        _set_progress(db, doc.id, 0.45, chunk_count=len(all_chunks))
        logger.info("Doc %s: %d total chunks generated", document_id, len(all_chunks))

        if not all_chunks:
            doc.status = DocumentStatus.failed
            doc.error = "No extractable text found in this document (scanned PDFs need Tesseract OCR)."
            db.commit()
            return

        # 3. Embed in batches (0.45 -> 0.95)
        chunk_rows: list[Chunk] = []
        total_batches = (len(all_chunks) + EMBED_BATCH_SIZE - 1) // EMBED_BATCH_SIZE
        embeddings_service_available = True
        embed_failed = False  # any batch failed -> keyword-only (degraded) document

        for batch_idx, batch_start in enumerate(range(0, len(all_chunks), EMBED_BATCH_SIZE)):
            batch = all_chunks[batch_start : batch_start + EMBED_BATCH_SIZE]
            batch_texts = [
                contextual_embed_input(doc.original_name, page_no, location, content)
                for page_no, content, location in batch
            ]

            vectors = None
            if embeddings_service_available:
                try:
                    vectors = embed_texts_sync(batch_texts, task="document")
                except Exception as embed_exc:
                    logger.warning(
                        "Doc %s: Embedding batch %d/%d failed (reason=embed_failed %s); indexing remaining chunks without vectors for BM25",
                        document_id,
                        batch_idx + 1,
                        total_batches,
                        embed_exc,
                    )
                    embeddings_service_available = False
                    embed_failed = True
                    vectors = [None] * len(batch)
            else:
                vectors = [None] * len(batch)

            for (page_no, content, location), vector in zip(batch, vectors, strict=False):
                chunk_rows.append(
                    Chunk(
                        document_id=doc.id,
                        page=page_no,
                        location=location,
                        chunk_index=len(chunk_rows),
                        content=content,
                        token_estimate=estimate_tokens(content),
                        embedding=vector,
                        embedding_model=embedding_version() if vector is not None else None,
                    )
                )

            progress_val = 0.45 + 0.50 * ((batch_start + len(batch)) / len(all_chunks))
            _set_progress(db, doc.id, min(progress_val, 0.95))

        # 4. Persist chunks & finalize (0.95 -> 1.0)
        db.execute(delete(Chunk).where(Chunk.document_id == uuidlib.UUID(document_id)))
        for row in chunk_rows:
            db.add(row)

        doc = db.get(Document, uuidlib.UUID(document_id))
        if doc is not None:
            doc.status = DocumentStatus.ready
            doc.progress = 1.0
            doc.chunk_count = len(chunk_rows)
            doc.error = None
            # Embedding failed but the text index exists: serve keyword-only
            # search instead of failing the whole ingestion.
            doc.degraded = embed_failed
            db.commit()
            _fire_webhook(doc.owner_id, "document.ready", {
                "document_id": str(doc.id),
                "document_name": doc.original_name,
                "chunk_count": len(chunk_rows),
            })

        logger.info(
            "Doc %s ready: %d chunks across %d sections processed in %.1fs",
            document_id,
            len(chunk_rows),
            len(sections),
            time.time() - started,
        )

    except Exception:
        logger.exception("Processing failed for doc %s", document_id)
        try:
            db.rollback()
            doc = db.get(Document, uuidlib.UUID(document_id))
            if doc is not None:
                doc.status = DocumentStatus.failed
                doc.error = "Document processing failed. Please retry or contact support."
                db.commit()
                _fire_webhook(doc.owner_id, "document.failed", {
                    "document_id": str(doc.id),
                    "document_name": doc.original_name,
                    "error": doc.error,
                })
        except Exception as inner_exc:
            logger.error("Failed to update failed status in DB: %s", inner_exc)
    finally:
        # The corpus changed: cached answers may reference outdated content.
        _invalidate_answer_cache(db)
        db.close()
        resources.close()
        if lock_connection is not None:
            try:
                lock_connection.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": lock_id})
                lock_connection.commit()
            finally:
                lock_connection.close()


def _fire_webhook(owner_id, event: str, payload: dict) -> None:
    """Best-effort webhook dispatch on the worker path (sync, never raises)."""
    try:
        from app.services.webhooks import dispatch_event_sync

        dispatch_event_sync(owner_id, event, payload)
    except Exception:
        logger.warning("Webhook dispatch for %s failed", event, exc_info=True)


def _invalidate_answer_cache(db: Session) -> None:
    """Corpus changed: drop cached answers so stale content can never be served."""
    try:
        from sqlalchemy import delete

        from app.db.models import SemanticCacheEntry

        db.execute(delete(SemanticCacheEntry))
        db.commit()
    except Exception as exc:
        logger.debug("Semantic cache invalidation skipped: %s", exc)
        try:
            db.rollback()
        except Exception:
            logger.warning("Cache rollback failed", exc_info=True)


@celery_app.task(name="process_document", bind=True, max_retries=1)
def process_document(self, document_id: str) -> None:
    process_document_core(document_id)


def reembed_document_core(document_id: str, batch_size: int = EMBED_BATCH_SIZE) -> None:
    """Re-embed the stored chunks of one document in place — no re-parsing.

    Fixes degraded (keyword-only) documents and applies new embedding settings
    (task prefixes, contextual headers) after a config change. Resumable by
    design: batches commit as they complete, so a crashed run can simply be
    re-triggered. Raises on hard failures the caller should see; a failed batch
    leaves the document degraded=True with its text index intact.
    """
    db = _session_factory()()
    try:
        doc = db.get(Document, uuidlib.UUID(document_id))
        if doc is None:
            logger.warning("Re-embed skipped: document %s not found", document_id)
            return
        doc.status = DocumentStatus.processing
        doc.progress = 0.05
        db.commit()

        chunks = (
            db.query(Chunk)
            .filter(Chunk.document_id == doc.id)
            .order_by(Chunk.chunk_index)
            .all()
        )
        embed_failed = False
        total = len(chunks)
        for batch_start in range(0, total, batch_size):
            batch = chunks[batch_start : batch_start + batch_size]
            try:
                vectors = embed_texts_sync(
                    [
                        contextual_embed_input(doc.original_name, c.page, c.location, c.content)
                        for c in batch
                    ],
                    task="document",
                )
            except Exception:
                logger.warning(
                    "Doc %s: re-embed batch failed (reason=embed_failed); keeping keyword-only index",
                    document_id,
                    exc_info=True,
                )
                embed_failed = True
                break
            for chunk, vector in zip(batch, vectors, strict=True):
                chunk.embedding = vector
                chunk.embedding_model = embedding_version()
            doc.progress = min(0.95, 0.05 + 0.90 * ((batch_start + len(batch)) / max(total, 1)))
            db.commit()

        doc = db.get(Document, uuidlib.UUID(document_id))
        if doc is not None:
            doc.status = DocumentStatus.ready
            doc.progress = 1.0
            doc.degraded = embed_failed
            doc.error = None if not embed_failed else doc.error
            db.commit()
        _invalidate_answer_cache(db)
        logger.info(
            "Doc %s re-embedded: chunks=%d version=%s degraded=%s",
            document_id, total, embedding_version(), embed_failed,
        )
    except Exception:
        logger.exception("Re-embed failed for doc %s", document_id)
        try:
            db.rollback()
            doc = db.get(Document, uuidlib.UUID(document_id))
            if doc is not None:
                # The previous text index is untouched; keep the document usable.
                doc.status = DocumentStatus.ready
                doc.degraded = True
                doc.error = "Re-embedding failed; search is keyword-only for now."
                db.commit()
        except Exception:
            logger.error("Failed to record re-embed failure in DB", exc_info=True)
    finally:
        db.close()


@celery_app.task(name="reembed_document", bind=True, max_retries=1)
def reembed_document(self, document_id: str) -> None:
    reembed_document_core(document_id)


def process_extraction_job_core(job_id: str) -> None:
    """Run a structured extraction job on the worker path (sync context).

    DB access stays sync; the shared extraction core runs in a fresh event loop
    whose ask_json bridges to the sync OpenAI client via to_thread.
    """
    import asyncio

    from app.services.extraction import Excerpt, run_extraction_core
    from app.services.llm import sync_llm_client

    db = _session_factory()()
    try:
        job = db.get(ExtractionJob, uuidlib.UUID(job_id))
        if job is None:
            logger.error("Extraction job %s not found", job_id)
            return
        job.status = ExtractionJobStatus.running
        job.error = None
        db.commit()

        settings = get_settings()
        excerpts: list[Excerpt] = []
        for doc_id in job.document_ids or []:
            doc = db.get(Document, uuidlib.UUID(str(doc_id)))
            if doc is None or doc.status != DocumentStatus.ready:
                continue
            chunks = (
                db.query(Chunk)
                .filter(Chunk.document_id == doc.id)
                .order_by(Chunk.chunk_index)
                .limit(settings.extract_max_chunks_per_doc)
                .all()
            )
            for chunk in chunks:
                excerpts.append(
                    Excerpt(
                        index=len(excerpts) + 1,
                        document_name=doc.original_name,
                        location=chunk.location or f"Page {chunk.page}",
                        content=chunk.content,
                    )
                )

        schema = job.schema_json or {}
        client = sync_llm_client()

        def _sync_ask(messages: list[dict]) -> str:
            response = client.chat.completions.create(
                model=settings.llm_model,
                messages=messages,
                temperature=0.0,
                max_tokens=settings.extract_max_tokens,
                stream=False,
            )
            return response.choices[0].message.content or ""

        async def _ask_json(messages: list[dict]) -> str:
            return await asyncio.to_thread(_sync_ask, messages)

        result = asyncio.run(
            run_extraction_core(schema, excerpts, _ask_json, job.template)
        )
        job.result = {
            "data": result.data,
            "confidence": result.confidence,
            "citations": result.citations,
            "template": result.template,
        }
        job.confidence = result.confidence
        job.status = ExtractionJobStatus.completed
        db.commit()
        logger.info("Extraction job %s completed (%d excerpts)", job_id, len(excerpts))

    except Exception:
        logger.exception("Extraction job %s failed", job_id)
        try:
            db.rollback()
            job = db.get(ExtractionJob, uuidlib.UUID(job_id))
            if job is not None:
                job.status = ExtractionJobStatus.failed
                job.error = "Extraction failed. Please retry or contact support."
                db.commit()
        except Exception as inner_exc:
            logger.error("Failed to record extraction failure: %s", inner_exc)
    finally:
        db.close()


@celery_app.task(name="process_extraction_job", bind=True, max_retries=1)
def process_extraction_job(self, job_id: str) -> None:
    process_extraction_job_core(job_id)


@celery_app.task(name="storage_cleanup")
def cleanup_storage():
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import select
    from app.db.models import StorageCleanup
    from app.services import storage
    with _session_factory()() as db:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        rows = db.scalars(select(StorageCleanup).where(StorageCleanup.created_at < cutoff).limit(100)).all()
        for row in rows:
            try:
                referenced = db.scalar(select(Document.id).where(Document.stored_path == row.locator).limit(1))
                if not referenced:
                    storage.delete(row.locator)
                db.delete(row)
                db.commit()
            except Exception:
                db.rollback()
                logger.warning("Object cleanup will retry", exc_info=True)


@celery_app.task(name="recover_ingestion")
def recover_ingestion():
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import select
    with _session_factory()() as db:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
        ids = db.scalars(select(Document.id).where(Document.status == DocumentStatus.processing, Document.updated_at < cutoff).limit(100)).all()
        for doc_id in ids:
            process_document.delay(str(doc_id))
