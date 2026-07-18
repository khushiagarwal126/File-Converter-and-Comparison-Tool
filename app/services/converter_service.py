"""
app/services/converter_service.py
───────────────────────────────────
Business logic for file format conversion.

Rules for service files:
  - No Flask imports. No request/response objects.
  - Pure Python: accept plain arguments, return ConversionResult or raise.
  - All HTTP concerns live in the endpoint layer.

Supported formats
─────────────────
    CSV, JSON, XML, XLSX, TXT, PDF

    PDF is read-only as a source (text extraction via pdfplumber).
    PDF is write-only as a target (text → PDF via reportlab).

Conversion strategy
────────────────────
    All tabular formats (CSV, JSON, XML, XLSX) pass through a pandas
    DataFrame as the canonical intermediate representation.

    TXT is treated as line-delimited plain text — when used as a source
    it produces a single-column DataFrame; when used as a target the
    DataFrame is serialised with to_string().

    PDF source  → raw text extracted, written to the target format as
                  best-effort plain text.
    PDF target  → the text content of the source is rendered onto A4
                  pages with ReportLab.

Public interface
────────────────
    result = convert_file(source_path, target_format, output_dir)
    # Returns ConversionResult on success.
    # Raises ConversionError on any failure.
"""

import csv
import io
import json
import os
import textwrap
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd
import pdfplumber
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas

from app.utils.logger import logger

# ─────────────────────────────────────────────────────────────────────────────
# Supported format registry
# ─────────────────────────────────────────────────────────────────────────────

#: Formats that can be used as a conversion source.
READABLE_FORMATS: frozenset = frozenset({"csv", "json", "xml", "xlsx", "txt", "pdf"})

#: Formats that can be used as a conversion target.
WRITABLE_FORMATS: frozenset = frozenset({"csv", "json", "xml", "xlsx", "txt", "pdf"})

#: All supported format strings (used for validation messages).
SUPPORTED_FORMATS: frozenset = READABLE_FORMATS | WRITABLE_FORMATS


# ─────────────────────────────────────────────────────────────────────────────
# Custom exception
# ─────────────────────────────────────────────────────────────────────────────

class ConversionError(Exception):
    """
    Raised by convert_file() on any failure.

    Attributes:
        code    – machine-readable error code, e.g. "FILE_NOT_FOUND"
        message – human-readable explanation
        status  – suggested HTTP status code for the endpoint to use
    """

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code    = code
        self.message = message
        self.status  = status


# ─────────────────────────────────────────────────────────────────────────────
# Result dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ConversionResult:
    """Returned by convert_file() on success."""

    conversion_id:   str   # UUID for this conversion job
    source_path:     str   # absolute path of the source file
    output_path:     str   # absolute path of the converted output file
    source_format:   str   # e.g. "csv"
    target_format:   str   # e.g. "json"
    source_filename: str   # original source filename
    output_filename: str   # generated output filename
    size_bytes:      int   # output file size in bytes

    def to_dict(self) -> dict:
        return {
            "conversion_id":   self.conversion_id,
            "source_path":     self.source_path,
            "output_path":     self.output_path,
            "source_format":   self.source_format,
            "target_format":   self.target_format,
            "source_filename": self.source_filename,
            "output_filename": self.output_filename,
            "size_bytes":      self.size_bytes,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def convert_file(
    source_path: str,
    target_format: str,
    output_dir: str,
) -> ConversionResult:
    """
    Convert a file from its current format to *target_format*.

    Args:
        source_path:   Absolute or relative path to the uploaded source file.
        target_format: Desired output format (csv / json / xml / xlsx / txt / pdf).
        output_dir:    Directory where the converted file will be saved.

    Returns:
        ConversionResult with all job metadata.

    Raises:
        ConversionError on validation failure or conversion error.
    """
    target_format = target_format.strip().lower()

    # ── Validate source file ──────────────────────────────────────────────────
    src = Path(source_path)
    if not src.exists():
        raise ConversionError(
            "FILE_NOT_FOUND",
            f"Source file not found: '{source_path}'. "
            "Upload the file first via POST /api/v1/upload.",
            status=404,
        )
    if not src.is_file():
        raise ConversionError(
            "INVALID_SOURCE",
            f"'{source_path}' is not a regular file.",
        )

    source_format = src.suffix.lstrip(".").lower()
    if source_format not in READABLE_FORMATS:
        raise ConversionError(
            "UNSUPPORTED_SOURCE_FORMAT",
            f"'.{source_format}' cannot be read. "
            f"Supported source formats: {_fmt(READABLE_FORMATS)}.",
        )

    # ── Validate target format ────────────────────────────────────────────────
    if not target_format:
        raise ConversionError(
            "MISSING_TARGET_FORMAT",
            "Request body must include 'target_format'.",
        )
    if target_format not in WRITABLE_FORMATS:
        raise ConversionError(
            "UNSUPPORTED_TARGET_FORMAT",
            f"'{target_format}' is not a supported output format. "
            f"Supported target formats: {_fmt(WRITABLE_FORMATS)}.",
        )

    # ── Guard: same-format conversion is a no-op ──────────────────────────────
    if source_format == target_format:
        raise ConversionError(
            "SAME_FORMAT",
            f"Source and target formats are both '{source_format}'. "
            "No conversion needed.",
        )

    # ── Prepare output path ───────────────────────────────────────────────────
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    conversion_id   = str(uuid.uuid4())
    output_filename = f"{conversion_id}_{src.stem}.{target_format}"
    output_path     = Path(output_dir) / output_filename

    logger.info(
        "Conversion started | id=%s | %s → %s | source='%s'",
        conversion_id, source_format, target_format, src.name,
    )

    # ── Dispatch to the correct converter ─────────────────────────────────────
    try:
        if source_format == "pdf":
            _convert_from_pdf(src, output_path, target_format)
        elif target_format == "pdf":
            _convert_to_pdf(src, output_path, source_format)
        else:
            _convert_tabular(src, output_path, source_format, target_format)
    except ConversionError:
        raise
    except Exception as exc:
        logger.error(
            "Conversion failed | id=%s | %s", conversion_id, exc, exc_info=True
        )
        raise ConversionError(
            "CONVERSION_FAILED",
            f"Conversion from '{source_format}' to '{target_format}' failed: {exc}",
            status=500,
        )

    size_bytes = output_path.stat().st_size
    logger.info(
        "Conversion complete | id=%s | output='%s' | %d bytes",
        conversion_id, output_path.name, size_bytes,
    )

    return ConversionResult(
        conversion_id   = conversion_id,
        source_path     = str(src.resolve()),
        output_path     = str(output_path.resolve()),
        source_format   = source_format,
        target_format   = target_format,
        source_filename = src.name,
        output_filename = output_filename,
        size_bytes      = size_bytes,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tabular conversion  (CSV / JSON / XML / XLSX / TXT)
# ─────────────────────────────────────────────────────────────────────────────

def _convert_tabular(
    src: Path,
    dest: Path,
    source_fmt: str,
    target_fmt: str,
) -> None:
    """
    Read the source into a pandas DataFrame, write to the target format.

    Reader dispatch:
        csv   → pd.read_csv
        json  → pd.read_json  (expects array-of-objects or records orient)
        xml   → pd.read_xml
        xlsx  → pd.read_excel (openpyxl engine)
        txt   → read lines, produce single-column DataFrame

    Writer dispatch:
        csv   → DataFrame.to_csv  (no index)
        json  → DataFrame.to_json (records orient, indent 2)
        xml   → DataFrame.to_xml  (no index)
        xlsx  → DataFrame.to_excel (openpyxl, no index)
        txt   → DataFrame.to_string (no index) or raw lines
    """
    df = _read_to_dataframe(src, source_fmt)
    _write_from_dataframe(df, dest, target_fmt)


def _read_to_dataframe(src: Path, fmt: str) -> pd.DataFrame:
    """Read a supported tabular file into a DataFrame."""

    if fmt == "csv":
        return pd.read_csv(src, encoding="utf-8-sig")  # utf-8-sig strips BOM

    if fmt == "json":
        # Accept both array-of-objects and records orient
        raw = src.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, list):
            return pd.DataFrame(data)
        if isinstance(data, dict):
            # Flatten one level: {"key": [...]} → use the first list value
            for val in data.values():
                if isinstance(val, list):
                    return pd.DataFrame(val)
            return pd.DataFrame([data])
        raise ConversionError(
            "PARSE_ERROR",
            "JSON source must be an array of objects or an object wrapping one.",
        )

    if fmt == "xml":
        return pd.read_xml(src, encoding="utf-8")

    if fmt == "xlsx":
        return pd.read_excel(src, engine="openpyxl")

    if fmt == "txt":
        lines = src.read_text(encoding="utf-8").splitlines()
        # Attempt to detect delimiter in first non-empty line
        non_empty = [l for l in lines if l.strip()]
        if not non_empty:
            raise ConversionError("EMPTY_FILE", "TXT source file has no content.")
        sample = non_empty[0]
        delimiter = _detect_delimiter(sample)
        if delimiter and len(non_empty) > 1:
            # Treat as delimited text: first line = header
            reader = csv.DictReader(non_empty, delimiter=delimiter)
            return pd.DataFrame(list(reader))
        # Unstructured text → single column "line"
        return pd.DataFrame({"line": non_empty})

    raise ConversionError("UNSUPPORTED_SOURCE_FORMAT", f"Cannot read format: {fmt}")


def _write_from_dataframe(df: pd.DataFrame, dest: Path, fmt: str) -> None:
    """Write a DataFrame to the target file format."""

    if fmt == "csv":
        df.to_csv(dest, index=False, encoding="utf-8")
        return

    if fmt == "json":
        dest.write_text(
            df.to_json(orient="records", indent=2, force_ascii=False),
            encoding="utf-8",
        )
        return

    if fmt == "xml":
        dest.write_text(
            df.to_xml(index=False),
            encoding="utf-8",
        )
        return

    if fmt == "xlsx":
        df.to_excel(dest, index=False, engine="openpyxl")
        return

    if fmt == "txt":
        dest.write_text(
            df.to_string(index=False),
            encoding="utf-8",
        )
        return

    raise ConversionError("UNSUPPORTED_TARGET_FORMAT", f"Cannot write format: {fmt}")


# ─────────────────────────────────────────────────────────────────────────────
# PDF ← → everything
# ─────────────────────────────────────────────────────────────────────────────

def _convert_from_pdf(src: Path, dest: Path, target_fmt: str) -> None:
    """
    Extract text from a PDF and write it to *target_fmt*.

    Strategy:
        pdfplumber extracts text page-by-page.
        The combined text is then treated like a TXT source and
        passed through _write_from_dataframe() for structured targets,
        or written raw for TXT.
    """
    text = _extract_pdf_text(src)

    if target_fmt == "txt":
        dest.write_text(text, encoding="utf-8")
        return

    # For structured targets, try to parse the extracted text as CSV first.
    # Fall back to line-per-row single-column DataFrame.
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        raise ConversionError(
            "EMPTY_PDF",
            "No extractable text found in the PDF. "
            "Scanned/image-only PDFs are not supported.",
        )

    delimiter = _detect_delimiter(lines[0]) if lines else None
    if delimiter and len(lines) > 1:
        reader = csv.DictReader(lines, delimiter=delimiter)
        df = pd.DataFrame(list(reader))
    else:
        df = pd.DataFrame({"line": lines})

    _write_from_dataframe(df, dest, target_fmt)


def _convert_to_pdf(src: Path, dest: Path, source_fmt: str) -> None:
    """
    Convert a file to PDF using ReportLab.

    For tabular formats: read into DataFrame, render as a plain-text table.
    For TXT: render lines directly onto A4 pages.
    """
    if source_fmt == "txt":
        text = src.read_text(encoding="utf-8")
    else:
        df = _read_to_dataframe(src, source_fmt)
        text = df.to_string(index=False)

    _render_text_to_pdf(text, dest)


def _extract_pdf_text(src: Path) -> str:
    """Extract all text from a PDF file using pdfplumber."""
    pages_text = []
    with pdfplumber.open(str(src)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                pages_text.append(page_text)
    return "\n".join(pages_text)


def _render_text_to_pdf(text: str, dest: Path) -> None:
    """
    Render plain text onto A4 pages and save to *dest*.

    Handles:
      - Long lines → wrapped at 100 characters
      - Page overflow → automatic page break
    """
    PAGE_W, PAGE_H = A4
    MARGIN        = 2 * cm
    FONT_NAME     = "Helvetica"
    FONT_SIZE     = 9
    LINE_HEIGHT   = FONT_SIZE * 1.4
    USABLE_W      = PAGE_W - 2 * MARGIN
    USABLE_H      = PAGE_H - 2 * MARGIN
    CHARS_PER_ROW = int(USABLE_W / (FONT_SIZE * 0.55))  # approximate

    buf = io.BytesIO()
    c   = canvas.Canvas(str(dest), pagesize=A4)
    c.setFont(FONT_NAME, FONT_SIZE)

    y = PAGE_H - MARGIN

    def new_page():
        nonlocal y
        c.showPage()
        c.setFont(FONT_NAME, FONT_SIZE)
        y = PAGE_H - MARGIN

    for raw_line in text.splitlines():
        # Wrap long lines
        wrapped = textwrap.wrap(raw_line, width=CHARS_PER_ROW) or [""]
        for segment in wrapped:
            if y < MARGIN + LINE_HEIGHT:
                new_page()
            c.drawString(MARGIN, y, segment)
            y -= LINE_HEIGHT

    c.save()


# ─────────────────────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────────────────────

def _detect_delimiter(sample_line: str) -> Optional[str]:
    """
    Heuristically detect the delimiter in a single text line.
    Returns the delimiter character or None if the line is unstructured.
    """
    for delim in (",", "\t", ";", "|"):
        if delim in sample_line:
            return delim
    return None


def _fmt(extensions: frozenset) -> str:
    """Return a human-readable sorted list of format names."""
    return ", ".join(sorted(extensions))