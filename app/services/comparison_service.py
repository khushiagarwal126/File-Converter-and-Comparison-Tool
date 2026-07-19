"""
app/services/comparison_service.py
────────────────────────────────────
Business logic for file-to-file comparison.

Rules for service files:
  - No Flask imports. No request/response objects.
  - Pure Python: accept plain arguments, return ComparisonResult or raise.
  - All HTTP concerns live in the endpoint layer.

Supported format pairs
───────────────────────
    CSV vs CSV   — row-level + field-level diff via pandas
    JSON vs JSON — key-level diff (normalised, nested-aware)
    XML vs XML   — element-level diff (tag, attribute, text)
    TXT vs TXT   — line-level diff via difflib
    XLSX vs XLSX — sheet-level, row-level + field-level diff via pandas
    PDF vs PDF   — extracted text compared line-by-line via difflib

Comparison strategy
────────────────────
    Each format has a dedicated reader that returns a normalised Python
    structure (list of dicts for tabular; list of strings for text;
    recursive dict for JSON/XML).

    A shared _similarity() helper turns any two lists of strings into a
    0-100 similarity percentage using difflib.SequenceMatcher, which is
    format-agnostic and battle-tested.

    Field-level diff for tabular formats aligns rows by position, then
    compares each cell. Differences are classified as:
        ADDED   — row or field present in file_b but not file_a
        REMOVED — row or field present in file_a but not file_b
        CHANGED — same position/key, different value

Public interface
────────────────
    result = compare_files(file_a_path, file_b_path)
    # Returns ComparisonResult on success.
    # Raises ComparisonError on validation failure or parse error.
"""

import csv
import json
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import pdfplumber

from app.utils.logger import logger

# ─────────────────────────────────────────────────────────────────────────────
# Format registry
# ─────────────────────────────────────────────────────────────────────────────

COMPARABLE_FORMATS: frozenset = frozenset(
    {"csv", "json", "xml", "txt", "xlsx", "pdf"}
)


# ─────────────────────────────────────────────────────────────────────────────
# Custom exception
# ─────────────────────────────────────────────────────────────────────────────

class ComparisonError(Exception):
    """
    Raised by compare_files() on validation failure or parse error.

    Attributes:
        code    – machine-readable error code
        message – human-readable explanation
        status  – suggested HTTP status code
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
class ComparisonResult:
    """Returned by compare_files() on success."""

    comparison_id:        str
    file_a:               str
    file_b:               str
    format:               str
    files_match:          bool
    similarity_percentage: float
    total_differences:    int
    summary:              str
    differences:          List[Dict[str, Any]] = field(default_factory=list)
    compared_at:          str = ""

    def __post_init__(self):
        if not self.compared_at:
            self.compared_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "comparison_id":         self.comparison_id,
            "file_a":                self.file_a,
            "file_b":                self.file_b,
            "format":                self.format,
            "files_match":           self.files_match,
            "similarity_percentage": round(self.similarity_percentage, 2),
            "total_differences":     self.total_differences,
            "summary":               self.summary,
            "differences":           self.differences,
            "compared_at":           self.compared_at,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def compare_files(
    file_a_path: str,
    file_b_path: str,
) -> ComparisonResult:
    """
    Compare two files of the same format and return a ComparisonResult.

    Args:
        file_a_path: Path to the first file (baseline).
        file_b_path: Path to the second file (comparand).

    Returns:
        ComparisonResult with full diff details.

    Raises:
        ComparisonError on validation failure or parse error.
    """
    path_a = Path(file_a_path)
    path_b = Path(file_b_path)

    # ── File existence ────────────────────────────────────────────────────────
    if not path_a.exists():
        raise ComparisonError(
            "FILE_A_NOT_FOUND",
            f"File A not found: '{file_a_path}'. "
            "Upload it first via POST /api/v1/upload.",
            status=404,
        )
    if not path_b.exists():
        raise ComparisonError(
            "FILE_B_NOT_FOUND",
            f"File B not found: '{file_b_path}'. "
            "Upload it first via POST /api/v1/upload.",
            status=404,
        )

    # ── Extension validation ──────────────────────────────────────────────────
    ext_a = path_a.suffix.lstrip(".").lower()
    ext_b = path_b.suffix.lstrip(".").lower()

    if ext_a not in COMPARABLE_FORMATS:
        raise ComparisonError(
            "UNSUPPORTED_FORMAT_A",
            f"'.{ext_a}' is not supported for comparison. "
            f"Supported: {', '.join(sorted(COMPARABLE_FORMATS))}.",
        )
    if ext_b not in COMPARABLE_FORMATS:
        raise ComparisonError(
            "UNSUPPORTED_FORMAT_B",
            f"'.{ext_b}' is not supported for comparison. "
            f"Supported: {', '.join(sorted(COMPARABLE_FORMATS))}.",
        )

    # ── Format must match ─────────────────────────────────────────────────────
    if ext_a != ext_b:
        raise ComparisonError(
            "FORMAT_MISMATCH",
            f"Both files must have the same format. "
            f"File A is '.{ext_a}', File B is '.{ext_b}'. "
            "Convert one of them first via POST /api/v1/convert.",
        )

    fmt = ext_a
    comparison_id = str(uuid.uuid4())

    logger.info(
        "Comparison started | id=%s | format=%s | a='%s' | b='%s'",
        comparison_id, fmt, path_a.name, path_b.name,
    )

    # ── Dispatch to format-specific comparator ────────────────────────────────
    try:
        dispatcher = {
            "csv":  _compare_csv,
            "xlsx": _compare_xlsx,
            "json": _compare_json,
            "xml":  _compare_xml,
            "txt":  _compare_txt,
            "pdf":  _compare_pdf,
        }
        files_match, similarity, differences = dispatcher[fmt](path_a, path_b)
    except ComparisonError:
        raise
    except Exception as exc:
        logger.error(
            "Comparison failed | id=%s | %s", comparison_id, exc, exc_info=True
        )
        raise ComparisonError(
            "COMPARISON_FAILED",
            f"Comparison failed unexpectedly: {exc}",
            status=500,
        )

    total_differences = len(differences)
    summary = _build_summary(
        fmt, path_a.name, path_b.name,
        files_match, similarity, total_differences,
    )

    result = ComparisonResult(
        comparison_id         = comparison_id,
        file_a                = str(path_a.resolve()),
        file_b                = str(path_b.resolve()),
        format                = fmt,
        files_match           = files_match,
        similarity_percentage = similarity,
        total_differences     = total_differences,
        summary               = summary,
        differences           = differences,
    )

    logger.info(
        "Comparison complete | id=%s | match=%s | similarity=%.1f%% | diffs=%d",
        comparison_id, files_match, similarity, total_differences,
    )

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Format-specific comparators
# Each returns (files_match: bool, similarity: float, differences: list)
# ─────────────────────────────────────────────────────────────────────────────

def _compare_csv(
    path_a: Path, path_b: Path
) -> tuple[bool, float, List[Dict]]:
    """
    Row-level and field-level comparison of two CSV files.

    Strategy:
        1. Parse both files into lists of dicts (header = keys).
        2. Align rows by position.
        3. Compare each field; record CHANGED / ADDED / REMOVED diffs.
        4. Compute similarity from flat cell-value lists.
    """
    rows_a = _read_csv(path_a)
    rows_b = _read_csv(path_b)
    return _diff_tabular(rows_a, rows_b, "csv")


def _compare_xlsx(
    path_a: Path, path_b: Path
) -> tuple[bool, float, List[Dict]]:
    """Row-level and field-level comparison of two XLSX files (first sheet)."""
    df_a = pd.read_excel(path_a, engine="openpyxl")
    df_b = pd.read_excel(path_b, engine="openpyxl")
    rows_a = df_a.astype(str).to_dict(orient="records")
    rows_b = df_b.astype(str).to_dict(orient="records")
    return _diff_tabular(rows_a, rows_b, "xlsx")


def _compare_json(
    path_a: Path, path_b: Path
) -> tuple[bool, float, List[Dict]]:
    """
    Key-level comparison of two JSON files.

    Strategy:
        - Parse both into Python objects.
        - Serialise to normalised (sorted-key) JSON strings for similarity.
        - Flatten both to {dot.path: value} dicts and diff key-by-key.
    """
    raw_a = json.loads(path_a.read_text(encoding="utf-8"))
    raw_b = json.loads(path_b.read_text(encoding="utf-8"))

    flat_a = _flatten_json(raw_a)
    flat_b = _flatten_json(raw_b)

    differences: List[Dict] = []
    all_keys = sorted(set(flat_a) | set(flat_b))

    for key in all_keys:
        if key not in flat_a:
            differences.append({
                "type": "ADDED",
                "key":  key,
                "file_a_value": None,
                "file_b_value": str(flat_b[key]),
            })
        elif key not in flat_b:
            differences.append({
                "type": "REMOVED",
                "key":  key,
                "file_a_value": str(flat_a[key]),
                "file_b_value": None,
            })
        elif str(flat_a[key]) != str(flat_b[key]):
            differences.append({
                "type": "CHANGED",
                "key":  key,
                "file_a_value": str(flat_a[key]),
                "file_b_value": str(flat_b[key]),
            })

    # Similarity: compare normalised JSON strings
    str_a = json.dumps(raw_a, sort_keys=True, ensure_ascii=False)
    str_b = json.dumps(raw_b, sort_keys=True, ensure_ascii=False)
    similarity = _similarity(str_a.splitlines(), str_b.splitlines())
    files_match = (len(differences) == 0)

    return files_match, similarity, differences


def _compare_xml(
    path_a: Path, path_b: Path
) -> tuple[bool, float, List[Dict]]:
    """
    Element-level comparison of two XML files.

    Strategy:
        - Parse both with xml.etree.ElementTree.
        - Flatten each tree into {xpath: text/attrib} dict.
        - Diff key-by-key.
        - Similarity from flattened text values as string lists.
    """
    tree_a = ET.parse(str(path_a))
    tree_b = ET.parse(str(path_b))

    flat_a = _flatten_xml(tree_a.getroot())
    flat_b = _flatten_xml(tree_b.getroot())

    differences: List[Dict] = []
    all_keys = sorted(set(flat_a) | set(flat_b))

    for key in all_keys:
        if key not in flat_a:
            differences.append({
                "type": "ADDED",
                "xpath": key,
                "file_a_value": None,
                "file_b_value": flat_b[key],
            })
        elif key not in flat_b:
            differences.append({
                "type": "REMOVED",
                "xpath": key,
                "file_a_value": flat_a[key],
                "file_b_value": None,
            })
        elif flat_a[key] != flat_b[key]:
            differences.append({
                "type": "CHANGED",
                "xpath": key,
                "file_a_value": flat_a[key],
                "file_b_value": flat_b[key],
            })

    lines_a = list(flat_a.values())
    lines_b = list(flat_b.values())
    similarity = _similarity(lines_a, lines_b)
    files_match = (len(differences) == 0)

    return files_match, similarity, differences


def _compare_txt(
    path_a: Path, path_b: Path
) -> tuple[bool, float, List[Dict]]:
    """
    Line-level comparison of two plain-text files using difflib.

    Each difference records the line number in file_a and file_b,
    the diff tag (equal / replace / insert / delete), and the values.
    """
    lines_a = path_a.read_text(encoding="utf-8").splitlines()
    lines_b = path_b.read_text(encoding="utf-8").splitlines()

    differences: List[Dict] = []
    matcher = SequenceMatcher(None, lines_a, lines_b)

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        differences.append({
            "type":         tag.upper(),    # REPLACE | INSERT | DELETE
            "file_a_lines": list(range(i1 + 1, i2 + 1)),
            "file_b_lines": list(range(j1 + 1, j2 + 1)),
            "file_a_value": lines_a[i1:i2],
            "file_b_value": lines_b[j1:j2],
        })

    similarity = _similarity(lines_a, lines_b)
    files_match = (len(differences) == 0)
    return files_match, similarity, differences


def _compare_pdf(
    path_a: Path, path_b: Path
) -> tuple[bool, float, List[Dict]]:
    """
    Text-layer comparison of two PDFs.

    Extracts text from each PDF using pdfplumber, then delegates
    to the same line-level diff logic as _compare_txt().
    """
    text_a = _extract_pdf_text(path_a)
    text_b = _extract_pdf_text(path_b)

    # Write extracted text to temporary in-memory paths and reuse _compare_txt
    lines_a = text_a.splitlines()
    lines_b = text_b.splitlines()

    if not lines_a and not lines_b:
        return True, 100.0, []

    differences: List[Dict] = []
    matcher = SequenceMatcher(None, lines_a, lines_b)

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        differences.append({
            "type":         tag.upper(),
            "file_a_lines": list(range(i1 + 1, i2 + 1)),
            "file_b_lines": list(range(j1 + 1, j2 + 1)),
            "file_a_value": lines_a[i1:i2],
            "file_b_value": lines_b[j1:j2],
        })

    similarity = _similarity(lines_a, lines_b)
    files_match = (len(differences) == 0)
    return files_match, similarity, differences


# ─────────────────────────────────────────────────────────────────────────────
# Shared tabular diff engine
# ─────────────────────────────────────────────────────────────────────────────

def _diff_tabular(
    rows_a: List[Dict],
    rows_b: List[Dict],
    fmt: str,
) -> tuple[bool, float, List[Dict]]:
    """
    Compare two lists-of-dicts (tabular data) row-by-row, field-by-field.

    Alignment is positional (row index).  When one file has more rows than
    the other, extra rows are reported as ADDED or REMOVED.
    """
    differences: List[Dict] = []
    max_rows = max(len(rows_a), len(rows_b))
    all_flat_a: List[str] = []
    all_flat_b: List[str] = []

    for i in range(max_rows):
        row_num = i + 1
        if i >= len(rows_a):
            # Extra row in file_b
            differences.append({
                "type":         "ADDED",
                "row":          row_num,
                "file_a_value": None,
                "file_b_value": rows_b[i],
            })
            all_flat_b.extend(str(v) for v in rows_b[i].values())
            continue
        if i >= len(rows_b):
            # Extra row in file_a
            differences.append({
                "type":         "REMOVED",
                "row":          row_num,
                "file_a_value": rows_a[i],
                "file_b_value": None,
            })
            all_flat_a.extend(str(v) for v in rows_a[i].values())
            continue

        # Both rows exist — compare field by field
        row_a = rows_a[i]
        row_b = rows_b[i]
        all_keys = sorted(set(row_a) | set(row_b))

        for key in all_keys:
            val_a = str(row_a.get(key, "")) if key in row_a else None
            val_b = str(row_b.get(key, "")) if key in row_b else None

            all_flat_a.append(val_a if val_a is not None else "")
            all_flat_b.append(val_b if val_b is not None else "")

            if key not in row_a:
                differences.append({
                    "type":         "ADDED",
                    "row":          row_num,
                    "field":        key,
                    "file_a_value": None,
                    "file_b_value": val_b,
                })
            elif key not in row_b:
                differences.append({
                    "type":         "REMOVED",
                    "row":          row_num,
                    "field":        key,
                    "file_a_value": val_a,
                    "file_b_value": None,
                })
            elif val_a != val_b:
                differences.append({
                    "type":         "CHANGED",
                    "row":          row_num,
                    "field":        key,
                    "file_a_value": val_a,
                    "file_b_value": val_b,
                })

    similarity = _similarity(all_flat_a, all_flat_b)
    files_match = (len(differences) == 0)
    return files_match, similarity, differences


# ─────────────────────────────────────────────────────────────────────────────
# Readers and helpers
# ─────────────────────────────────────────────────────────────────────────────

def _read_csv(path: Path) -> List[Dict]:
    """Parse a CSV into a list of dicts (DictReader)."""
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _flatten_json(
    obj: Any,
    prefix: str = "",
    result: Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    Recursively flatten a nested JSON object into dot-notation key paths.

    Examples:
        {"a": {"b": 1}}  → {"a.b": 1}
        {"x": [1, 2]}    → {"x.0": 1, "x.1": 2}
    """
    if result is None:
        result = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            _flatten_json(v, f"{prefix}.{k}" if prefix else k, result)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _flatten_json(v, f"{prefix}.{i}" if prefix else str(i), result)
    else:
        result[prefix] = obj
    return result


def _flatten_xml(
    element: ET.Element,
    prefix: str = "",
) -> Dict[str, str]:
    """
    Recursively flatten an XML element tree into slash-notation XPath-like keys.

    Both element text and attributes are captured.

    Example:
        <root><row><name>Alice</name></row></root>
        → {"root/row/name": "Alice"}
    """
    result: Dict[str, str] = {}
    tag = element.tag.split("}")[-1] if "}" in element.tag else element.tag
    current = f"{prefix}/{tag}" if prefix else tag

    # Element text
    text = (element.text or "").strip()
    if text:
        result[f"{current}/#text"] = text

    # Attributes
    for attr_name, attr_val in element.attrib.items():
        result[f"{current}/@{attr_name}"] = attr_val

    # Children — append an index to distinguish repeated tags
    child_counts: Dict[str, int] = {}
    for child in element:
        child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        idx = child_counts.get(child_tag, 0)
        child_counts[child_tag] = idx + 1
        child_prefix = f"{current}/{child_tag}[{idx}]"
        result.update(_flatten_xml(child, prefix=child_prefix.rsplit("/", 1)[0]))

    return result


def _extract_pdf_text(path: Path) -> str:
    """Extract all text from a PDF using pdfplumber, page-by-page."""
    pages = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
    return "\n".join(pages)


def _similarity(seq_a: List[str], seq_b: List[str]) -> float:
    """
    Return a 0-100 similarity percentage between two string sequences
    using difflib.SequenceMatcher (Ratcliff/Obershelp algorithm).

    Returns 100.0 when both sequences are empty (two empty files are equal).
    """
    if not seq_a and not seq_b:
        return 100.0
    if not seq_a or not seq_b:
        return 0.0
    ratio = SequenceMatcher(None, seq_a, seq_b).ratio()
    return ratio * 100.0


def _build_summary(
    fmt: str,
    name_a: str,
    name_b: str,
    files_match: bool,
    similarity: float,
    total_differences: int,
) -> str:
    """Build a human-readable summary string for the comparison result."""
    if files_match:
        return (
            f"Files '{name_a}' and '{name_b}' are identical "
            f"({fmt.upper()}, 100.00% match)."
        )
    return (
        f"Files '{name_a}' and '{name_b}' differ: "
        f"{total_differences} difference(s) found, "
        f"{similarity:.2f}% similarity ({fmt.upper()})."
    )