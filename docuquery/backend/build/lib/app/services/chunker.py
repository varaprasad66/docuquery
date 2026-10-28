"""Paragraph-aware text chunking.

Deterministic and dependency-free: merges paragraphs up to CHUNK_SIZE chars,
hard-splits oversized paragraphs on sentence boundaries, and carries
CHUNK_OVERLAP chars between consecutive chunks so answers spanning a boundary
stay retrievable.
"""

import re

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


def hard_split(text: str, chunk_size: int, overlap: int) -> list[str]:
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_BOUNDARY.split(text):
        if not sentence:
            continue
        if len(current) + len(sentence) + 1 <= chunk_size:
            current = f"{current} {sentence}".strip()
            continue
        if current:
            pieces.append(current)
        if len(sentence) > chunk_size:
            # Pathological unbroken text (URLs, tables): fixed-window split.
            step = max(chunk_size - overlap, 1)
            for start in range(0, len(sentence), step):
                pieces.append(sentence[start : start + chunk_size])
                if start + chunk_size >= len(sentence):
                    break
            current = ""
        else:
            current = sentence
    if current:
        pieces.append(current)
    return pieces


def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 150) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text).strip()
    if not text:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if len(para) > chunk_size:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(hard_split(para, chunk_size, overlap))
            continue
        if len(current) + len(para) + 2 <= chunk_size:
            current = f"{current}\n\n{para}".strip()
        else:
            if current:
                tail = current[-overlap:] if overlap else ""
                chunks.append(current)
                current = f"{tail}\n\n{para}".strip() if tail else para
            else:
                current = para
    if current:
        chunks.append(current)
    return chunks


def estimate_tokens(text: str) -> int:
    """Cheap token estimate; good enough for display and rough cost math."""
    return max(1, round(len(text) / 4))
