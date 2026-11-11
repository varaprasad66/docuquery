#!/usr/bin/env python3
"""Re-embed stored chunks WITHOUT re-parsing the source documents.

Run after changing embedding settings (EMBED_*_PREFIX, CONTEXTUAL_EMBEDDINGS)
or the embeddings model: finds chunks whose embedding_model tag no longer
matches the configured embedding_version() (or whose embedding is NULL), and
re-embeds them in batches, committing per batch so the run is resumable —
interrupt it any time and re-run to continue where it stopped.

Progress logs carry counts and chunk IDs only, never document text.
The semantic answer cache is invalidated when any vector changed.

Usage (from backend/ with the venv active, or anywhere DATABASE_URL resolves):
    python scripts/reindex_embeddings.py            # re-embed everything stale
    python scripts/reindex_embeddings.py --dry-run  # just report
    python scripts/reindex_embeddings.py --batch-size 64 --document-id <uuid>
"""

import argparse
import logging
import os
import sys
import time
import uuid as uuidlib
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("reindex")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--document-id", type=str, default=None, help="Limit to one document")
    parser.add_argument("--dry-run", action="store_true", help="Report counts, change nothing")
    parser.add_argument("--all", action="store_true", help="Re-embed every chunk, not just stale ones")
    args = parser.parse_args()

    from sqlalchemy import create_engine, select, delete
    from sqlalchemy.orm import Session

    from app.config import get_settings
    from app.db.models import Chunk, Document, SemanticCacheEntry
    from app.services.embeddings import embed_texts_sync, embedding_version
    from app.services.embeddings import contextual_embed_input

    settings = get_settings()
    target = embedding_version()
    engine = create_engine(settings.sync_database_url, pool_pre_ping=True)

    with Session(engine) as db:
        query = (
            select(Chunk, Document.original_name)
            .join(Document, Document.id == Chunk.document_id)
            .where(Document.status == "ready")
        )
        if not args.all:
            query = query.where(
                (Chunk.embedding_model != target) | (Chunk.embedding.is_(None))
            )
        if args.document_id:
            query = query.where(Chunk.document_id == uuidlib.UUID(args.document_id))
        rows = db.execute(query.order_by(Chunk.document_id, Chunk.chunk_index)).all()

        stale = sum(1 for chunk, _ in rows if chunk.embedding_model != target or chunk.embedding is None)
        logger.info(
            "reason=reindex_start target=%s chunks=%d stale=%d dry_run=%s",
            target, len(rows), stale, args.dry_run,
        )
        if args.dry_run or not rows:
            return 0

        vectors_changed = False
        started = time.time()
        done = 0
        for start in range(0, len(rows), args.batch_size):
            batch = rows[start : start + args.batch_size]
            inputs = [
                contextual_embed_input(name, chunk.page, chunk.location, chunk.content)
                for chunk, name in batch
            ]
            try:
                vectors = embed_texts_sync(inputs, task="document")
            except Exception as exc:
                logger.error("reason=embed_failed batch_start=%d error=%s", start, exc)
                logger.error("Fix the embedding endpoint, then re-run; progress is kept.")
                break
            for (chunk, _), vector in zip(batch, vectors, strict=True):
                chunk.embedding = vector
                chunk.embedding_model = target
            db.commit()
            vectors_changed = True
            done += len(batch)
            logger.info(
                "reason=reindex_progress done=%d/%d elapsed_s=%.1f chunk_ids=%s",
                done, len(rows), time.time() - started,
                [str(chunk.id)[:8] for chunk, _ in batch[:3]],
            )

        if vectors_changed:
            # Embeddings changed meaning: cached answers may cite stale evidence.
            db.execute(delete(SemanticCacheEntry))
            db.commit()
            logger.info("reason=cache_invalidated entries=deleted")
        logger.info("reason=reindex_done done=%d/%d target=%s", done, len(rows), target)
    engine.dispose()
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        # Keep stderr unbuffered for Ctrl+C mid-run on Windows consoles.
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.exit(main())
