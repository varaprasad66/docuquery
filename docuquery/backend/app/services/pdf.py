"""PDF text extraction with an OCR fallback for scanned pages.

Strategy per page:
  1. Try the embedded text layer (PyMuPDF) - fast and exact for digital PDFs.
  2. If the page yields too little text (scanned/image page), rasterize it and
     run Tesseract OCR.

Tesseract discovery: uses PATH if available, otherwise checks the standard
Windows install locations (winget UB-Mannheim package).
"""

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

import pymupdf as fitz  # PyMuPDF
from PIL import Image
from PIL.Image import Resampling

from app.config import get_settings

logger = logging.getLogger(__name__)

_TESSERACT_CANDIDATES = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
]


def _configure_tesseract() -> str | None:
    """Return the tesseract binary path, configuring pytesseract if needed."""
    if shutil.which("tesseract"):
        return "tesseract"
    for candidate in _TESSERACT_CANDIDATES:
        if Path(candidate).exists():
            import pytesseract

            pytesseract.pytesseract.tesseract_cmd = candidate
            return candidate
    return None


@dataclass
class PageText:
    page: int  # 1-based
    text: str
    method: str  # "text" | "ocr"


def extract_pages(pdf_path: Path) -> list[PageText]:
    """Extract text for every page, OCR-ing pages that have no text layer."""
    settings = get_settings()
    tesseract = _configure_tesseract()
    pages: list[PageText] = []

    with fitz.open(pdf_path) as doc:
        for i, page in enumerate(doc):
            text = (page.get_text("text") or "").strip()
            if len(text) >= settings.ocr_min_chars_per_page:
                pages.append(PageText(page=i + 1, text=text, method="text"))
                continue

            if tesseract is None:
                logger.warning("Page %d has no text layer but Tesseract is not installed", i + 1)
                pages.append(PageText(page=i + 1, text=text, method="text"))
                continue

            pages.append(PageText(page=i + 1, text=_ocr_page(page), method="ocr"))

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
