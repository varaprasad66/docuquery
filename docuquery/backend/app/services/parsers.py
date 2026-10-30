"""Multi-format document parsing behind a registry.

The registry maps a lowercase file extension to a parser function. Every parser
returns ``list[SectionText]`` — the same page/section structure the PDF/OCR path
has always produced — so the chunker, embedding pipeline, and citations keep
working unchanged. ``location`` carries the human-readable source position
("Page 3", "Slide 2", "Sheet 'Invoices' rows 5-24", "Section: 2.1 Scope") that
the frontend displays in place of a bare page number.

Validation is content-based, never extension-based: PDF magic bytes, OOXML zip
structure (signature entries word/ ppt/ xl/), and text/HTML/Markdown sniffing.
Office files are ZIP containers, so entry count and total uncompressed size are
capped before parsing to reject zip bombs.

PDF + OCR behavior is unchanged: parse_pdf delegates to services.pdf.
"""

import io
import logging
import re
import warnings
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings
from app.services import pdf as pdf_service

logger = logging.getLogger(__name__)

SectionProgressCb = Callable[[int, int], None]


@dataclass
class SectionText:
    index: int  # 1-based ordinal across the whole document
    text: str
    location: str  # human-readable source position, shown in citations
    method: str = "text"  # "text" | "ocr"


SUPPORTED_EXTENSIONS: set[str] = {".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm", ".txt", ".md"}

_ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_HTML_SNIFF = re.compile(
    r"<(!doctype|html|head|body|div|p|h[1-6]|table|br|section)\b", re.IGNORECASE
)
_MD_SNIFF = re.compile(r"^(#{1,6}\s+\S|```)", re.MULTILINE)
_EXT_LABELS = {
    ".pdf": "PDF",
    ".docx": "DOCX",
    ".pptx": "PPTX",
    ".xlsx": "XLSX",
    ".html": "HTML",
    ".htm": "HTML",
    ".md": "Markdown",
    ".txt": "text",
}


# --- Content sniffing -------------------------------------------------------


def _decode_text(raw: bytes) -> str:
    """Decode text content: UTF-8 (with BOM), then UTF-16 BOM, then latin-1."""
    if b"\x00" in raw:
        raise ValueError("Binary content is not supported for text formats.")
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _zip_office_kind(raw: bytes) -> str:
    """Identify an OOXML package by its structural signature entries."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names = zf.namelist()
    except zipfile.BadZipFile:
        return "zip"
    if any(n.startswith("word/") for n in names):
        return "docx"
    if any(n.startswith("ppt/") for n in names):
        return "pptx"
    if any(n.startswith("xl/") for n in names):
        return "xlsx"
    return "zip"


def sniff_kind_bytes(raw: bytes) -> str:
    """Content-based kind detection. Returns one of:
    pdf | docx | pptx | xlsx | zip | html | markdown | text (raises for binary text).
    """
    if raw.startswith(b"%PDF-"):
        return "pdf"
    if raw[:4] in _ZIP_SIGNATURES:
        return _zip_office_kind(raw)
    text = _decode_text(raw)  # raises ValueError on binary
    head = text[:4096]
    if _HTML_SNIFF.search(head):
        return "html"
    if _MD_SNIFF.search(text):
        return "markdown"
    return "text"


def _ext_kind(ext: str) -> set[str]:
    """Kinds that a file with this extension may legitimately contain."""
    if ext == ".pdf":
        return {"pdf"}
    if ext in (".docx", ".pptx", ".xlsx"):
        return {ext[1:]}
    if ext in (".html", ".htm"):
        return {"html", "text"}
    if ext == ".md":
        return {"markdown", "text"}
    return {"text", "markdown", "html"}  # .txt is the most permissive


def validate_content_kind(raw: bytes, ext: str) -> str:
    """Raise ValueError unless the content matches the claimed extension.

    Returns the detected kind (used by URL import to pick a stored extension).
    """
    ext = ext.lower()
    kind = sniff_kind_bytes(raw)
    if kind == "zip":
        raise ValueError("ZIP archives are not supported on their own.")
    if kind not in _ext_kind(ext):
        raise ValueError(
            f"File content does not look like a valid {_EXT_LABELS.get(ext, ext[1:].upper())} document."
        )
    return kind


def stored_extension_for_kind(kind: str, hint_ext: str | None = None) -> str | None:
    """Pick the stored-file extension for fetched content.

    ``hint_ext`` is the URL path's extension when it is a supported one; it can
    preserve .md/.txt/.htm for text content. Returns None when the kind cannot
    be ingested (an unidentifiable zip, for instance).
    """
    if kind == "pdf":
        return ".pdf"
    if kind in ("docx", "pptx", "xlsx"):
        return f".{kind}"
    if kind == "html":
        return hint_ext if hint_ext in (".html", ".htm") else ".html"
    if kind == "markdown":
        return ".txt" if hint_ext == ".txt" else ".md"
    if kind == "text":
        return hint_ext if hint_ext in (".txt", ".md") else ".txt"
    return None


def check_zip_safety(raw: bytes) -> None:
    """Reject zip bombs before any parser touches the archive.

    Caps come from config: MAX_ARCHIVE_ENTRIES and MAX_UNCOMPRESSED_MB.
    """
    settings = get_settings()
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            infos = zf.infolist()
    except zipfile.BadZipFile as exc:
        raise ValueError("Corrupt ZIP container.") from exc
    if len(infos) > settings.max_archive_entries:
        raise ValueError(
            f"Archive has too many entries ({len(infos)} > {settings.max_archive_entries})."
        )
    total_uncompressed = sum(info.file_size for info in infos)
    max_bytes = settings.max_uncompressed_mb * 1024 * 1024
    if total_uncompressed > max_bytes:
        raise ValueError(
            f"Archive expands to {total_uncompressed // (1024 * 1024)} MB, "
            f"exceeding the {settings.max_uncompressed_mb} MB safety limit."
        )


# --- Parsers ----------------------------------------------------------------


def parse_pdf(path: Path, progress_cb: SectionProgressCb | None = None) -> list[SectionText]:
    """Existing PDF + OCR pipeline, wrapped in SectionText. Behavior unchanged."""
    pages = pdf_service.extract_pages(path, progress_cb=progress_cb)
    return [
        SectionText(index=i, text=p.text, location=f"Page {p.page}", method=p.method)
        for i, p in enumerate(pages, start=1)
    ]


def _location_label(label: str) -> str:
    label = re.sub(r"\s+", " ", label).strip()
    return label[:80] + ("…" if len(label) > 80 else "")


def _iter_docx_blocks(doc) -> list:
    """Paragraphs and tables in true document order."""
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    blocks = []
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            blocks.append(Paragraph(child, doc))
        elif child.tag == qn("w:tbl"):
            blocks.append(Table(child, doc))
    return blocks


def _docx_table_text(table) -> str:
    rows = []
    for row in table.rows:
        cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
        rows.append(" | ".join(cells))
    return "\n".join(rows)


def parse_docx(path: Path) -> list[SectionText]:
    """Word documents: sections split at Heading styles; tables rendered as rows.

    Documents without headings become one "Document" section.
    """
    from docx import Document as DocxDocument
    from docx.text.paragraph import Paragraph

    doc = DocxDocument(str(path))
    sections: list[SectionText] = []
    parts: list[str] = []
    current_location = "Document"

    def flush() -> None:
        text = "\n\n".join(p for p in parts if p.strip())
        if text.strip():
            sections.append(SectionText(index=0, text=text, location=current_location))
        parts.clear()

    for block in _iter_docx_blocks(doc):
        if isinstance(block, Paragraph):
            text = block.text.strip()
            style_name = getattr(block.style, "name", "") or ""
            is_heading = text and style_name.lower().startswith(("heading", "title"))
            if is_heading:
                flush()
                current_location = _location_label(f"Section: {text}")
            elif text:
                parts.append(text)
        else:  # Table
            table_text = _docx_table_text(block)
            if table_text.strip():
                parts.append(table_text)
    flush()
    return sections


def parse_pptx(path: Path) -> list[SectionText]:
    """Slides: one section per non-empty slide, tables rendered as rows."""
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    prs = Presentation(str(path))
    sections: list[SectionText] = []

    def shape_text(shape) -> str:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            return "\n".join(shape_text(sub) for sub in shape.shapes)
        lines = []
        if getattr(shape, "has_text_frame", False):
            for para in shape.text_frame.paragraphs:
                line = "".join(run.text for run in para.runs).strip()
                if line:
                    lines.append(line)
        if getattr(shape, "has_table", False):
            for row in shape.table.rows:
                lines.append(" | ".join(cell.text.strip() for cell in row.cells))
        return "\n".join(lines)

    for slide_no, slide in enumerate(prs.slides, start=1):
        texts = [t for t in (shape_text(shape) for shape in slide.shapes) if t.strip()]
        if not texts:
            continue
        sections.append(SectionText(index=0, text="\n".join(texts), location=f"Slide {slide_no}"))
    return sections


def _xlsx_cell_str(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def parse_xlsx(path: Path) -> list[SectionText]:
    """Spreadsheets: rows become readable text per sheet, grouped into row-range
    sections with the header row repeated so every chunk stays self-describing."""
    from openpyxl import load_workbook

    settings = get_settings()
    group_size = max(1, settings.xlsx_rows_per_section)
    sections: list[SectionText] = []

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = load_workbook(path, read_only=True, data_only=True)
    try:
        for ws in wb.worksheets:
            header: list[str] | None = None
            buffered: list[tuple[int, str]] = []

            def flush(
                header: list[str] | None, buffered: list[tuple[int, str]], sheet: str = ws.title
            ) -> None:
                if not buffered:
                    return
                header_line = " | ".join(header) if header else ""
                lines = [f"Sheet '{sheet}'"]
                if header_line:
                    lines.append(f"Columns: {header_line}")
                lines.extend(f"Row {row_no}: {text}" for row_no, text in buffered)
                first, last = buffered[0][0], buffered[-1][0]
                sections.append(
                    SectionText(
                        index=0,
                        text="\n".join(lines),
                        location=f"Sheet '{sheet}' rows {first}-{last}",
                    )
                )
                buffered.clear()

            for row_no, row in enumerate(ws.iter_rows(values_only=True), start=1):
                cells = [_xlsx_cell_str(v) for v in row]
                if not any(cells):
                    continue
                if header is None:
                    header = [c or f"Column {i + 1}" for i, c in enumerate(cells)]
                    continue
                rendered = " | ".join(f"{h}: {v}" for h, v in zip(header, cells, strict=False) if v)
                if not rendered:
                    continue
                buffered.append((row_no, rendered))
                if len(buffered) >= group_size:
                    flush(header, buffered)
            flush(header, buffered)
    finally:
        wb.close()
    return sections


def _flush_html_section(sections: list[SectionText], parts: list[str], location: str) -> None:
    text = re.sub(r"[ \t]+", " ", "\n".join(parts)).strip()
    if text:
        sections.append(SectionText(index=0, text=text, location=location))
    parts.clear()


def parse_html(path: Path) -> list[SectionText]:
    """HTML: sections split at h1-h3; tables and lists flattened to text lines."""
    from bs4 import BeautifulSoup

    raw = path.read_bytes()
    soup = BeautifulSoup(_decode_text(raw), "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()

    sections: list[SectionText] = []
    parts: list[str] = []
    location = "Document"

    for el in soup.find_all(["h1", "h2", "h3", "p", "li", "pre", "blockquote", "table"]):
        name = (el.name or "").lower()
        if name in ("h1", "h2", "h3"):
            _flush_html_section(sections, parts, location)
            heading = el.get_text(" ", strip=True)
            location = _location_label(f"Section: {heading}") if heading else "Document"
        elif name == "table":
            if el.find_parent("table") is not None:
                continue
            for tr in el.find_all("tr"):
                cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
                if any(cells):
                    parts.append(" | ".join(cells))
        else:
            if el.find_parent("table") is not None:
                continue
            text = el.get_text(" ", strip=True)
            if text:
                parts.append(text)

    _flush_html_section(sections, parts, location)
    if not sections:
        body = soup.get_text(" ", strip=True)
        if body:
            sections.append(SectionText(index=0, text=body, location="Document"))
    return sections


_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def _split_markdown(text: str) -> list[tuple[str, str]]:
    """(location, text) pairs; ATX headings start new sections, frontmatter skipped."""
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                lines = lines[i + 1 :]
                break

    sections: list[tuple[str, list[str]]] = [("Document", [])]
    for line in lines:
        match = _MD_HEADING.match(line)
        if match:
            sections.append((_location_label(f"Section: {match.group(2)}"), []))
        else:
            sections[-1][1].append(line)
    return [(loc, "\n".join(parts)) for loc, parts in sections if "\n".join(parts).strip()]


def parse_md(path: Path) -> list[SectionText]:
    return [
        SectionText(index=0, text=text, location=location)
        for location, text in _split_markdown(_decode_text(path.read_bytes()))
    ]


def parse_txt(path: Path) -> list[SectionText]:
    text = re.sub(r"[ \t]+", " ", _decode_text(path.read_bytes())).strip()
    if text:
        return [SectionText(index=0, text=text, location="Document")]
    return []


# --- Registry ---------------------------------------------------------------


PARSERS: dict[str, Callable] = {
    ".pdf": parse_pdf,
    ".docx": parse_docx,
    ".pptx": parse_pptx,
    ".xlsx": parse_xlsx,
    ".html": parse_html,
    ".htm": parse_html,
    ".md": parse_md,
    ".txt": parse_txt,
}


def parse_document(
    path: Path,
    ext: str,
    progress_cb: SectionProgressCb | None = None,
) -> list[SectionText]:
    """Parse any supported file into normalized sections.

    Validates content against the claimed extension (magic bytes / zip structure /
    text sniffing) and applies the zip-bomb guard before dispatching to the
    parser registered for ``ext``. PDF + OCR behavior is unchanged.
    """
    ext = ext.lower().strip()
    if not ext.startswith("."):
        ext = f".{ext}"
    parser = PARSERS.get(ext)
    if parser is None:
        raise ValueError(
            f"Unsupported file type '{ext}'. Accepted: {', '.join(sorted(SUPPORTED_EXTENSIONS))}."
        )

    raw = path.read_bytes()
    validate_content_kind(raw, ext)
    if ext in (".docx", ".pptx", ".xlsx"):
        check_zip_safety(raw)

    if parser is parse_pdf:
        sections = parser(path, progress_cb=progress_cb)
    else:
        sections = parser(path)

    sections = [s for s in sections if s.text and s.text.strip()]
    if not sections:
        raise ValueError("No extractable text found in this document.")
    return [
        SectionText(index=i, text=s.text, location=s.location, method=s.method)
        for i, s in enumerate(sections, start=1)
    ]
