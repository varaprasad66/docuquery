"""PDF text extraction with an OCR fallback for scanned pages.

Strategy per page:
  1. Try the embedded text layer (PyMuPDF) - fast and exact for digital PDFs.
  2. If the page yields too little text (scanned/image page), rasterize it and
     run Tesseract OCR.

Tesseract discovery: uses PATH if available, otherwise checks the standard
Windows install locations (winget UB-Mannheim package).
"""

from collections.abc import Callable
import logging
from pathlib import Path
import shutil
from dataclasses import dataclass

from PIL import Image
from PIL.Image import Resampling
import pymupdf as fitz  # PyMuPDF

from app.config import get_settings

logger = logging.getLogger(__name__)

_TESSERACT_CANDIDATES = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    "/usr/bin/tesseract",
    "/usr/local/bin/tesseract",
]


def _configure_tesseract() -> str | None:
    """Return the tesseract binary path, configuring pytesseract if needed."""
    if shutil.which("tesseract"):
        return "tesseract"
    for candidate in _TESSERACT_CANDIDATES:
        if Path(candidate).exists():
            try:
                import pytesseract

                pytesseract.pytesseract.tesseract_cmd = candidate
                return candidate
            except Exception:
                pass
    return None


@dataclass
class PageText:
    page: int  # 1-based
    text: str
    method: str  # "text" | "ocr"


def _clean_text(text: str) -> str:
    """Remove null bytes and strip excess whitespace."""
    if not text:
        return ""
    return text.replace("\x00", "").strip()


def extract_pages(
    pdf_path: Path, progress_cb: Callable[[int, int], None] | None = None
) -> list[PageText]:
    """Extract text for every page, OCR-ing pages that have no text layer.

    Calls progress_cb(current_page_1_based, total_pages) if provided.
    """
    settings = get_settings()
    tesseract = _configure_tesseract()
    pages: list[PageText] = []

    with fitz.open(pdf_path) as doc:
        total = doc.page_count
        for i, page in enumerate(doc):
            page_no = i + 1
            raw_text = _clean_text(page.get_text("text") or "")
            if len(raw_text) >= settings.ocr_min_chars_per_page:
                pages.append(PageText(page=page_no, text=raw_text, method="text"))
            elif tesseract is None:
                logger.warning(
                    "Page %d has no text layer (%d chars) and Tesseract is not configured",
                    page_no,
                    len(raw_text),
                )
                pages.append(PageText(page=page_no, text=raw_text, method="text"))
            else:
                logger.info(
                    "Page %d has low text density (%d chars); running OCR...",
                    page_no,
                    len(raw_text),
                )
                try:
                    ocr_text = _ocr_page(page)
                    final_text = ocr_text if len(ocr_text) > len(raw_text) else raw_text
                    pages.append(
                        PageText(
                            page=page_no,
                            text=_clean_text(final_text),
                            method="ocr" if len(ocr_text) > len(raw_text) else "text",
                        )
                    )
                except Exception as exc:
                    logger.warning("OCR failed on page %d (%s); falling back to raw text", page_no, exc)
                    pages.append(PageText(page=page_no, text=raw_text, method="text"))

            if progress_cb:
                try:
                    progress_cb(page_no, total)
                except Exception:
                    pass

    return pages


def _ocr_page(page: "fitz.Page") -> str:
    settings = get_settings()
    pix = page.get_pixmap(dpi=settings.ocr_dpi, colorspace=fitz.csGRAY)
    img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    # Upscale small renders modestly; Tesseract reads >150 DPI best.
    if pix.width < 1000:
        scale = 1000 / pix.width
        img = img.resize((int(pix.width * scale), int(pix.height * scale)), Resampling.LANCZOS)

    import pytesseract

    return pytesseract.image_to_string(img, lang="eng", config="--psm 4").strip()


def page_count(pdf_path: Path) -> int:
    with fitz.open(pdf_path) as doc:
        return doc.page_count
