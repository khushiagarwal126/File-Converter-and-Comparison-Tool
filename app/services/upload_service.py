"""
app/services/upload_service.py
───────────────────────────────
Business logic for file uploads.

Rules for service files:
  - No Flask imports. No request/response objects.
  - Pure Python: accept plain arguments, return plain values or raise.
  - All HTTP concern (status codes, jsonify) lives in the endpoint layer.

Public interface
────────────────
    result = save_upload(file_storage, upload_dir, allowed_extensions, max_bytes)
    # result is a dict — passed straight into success_response(data=result)

    Raises UploadValidationError on any validation failure.
    The endpoint catches it and calls error_response().
"""

import uuid
import os
from dataclasses import dataclass
from pathlib import Path
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from app.utils.logger import logger


# ─────────────────────────────────────────────────────────────────────────────
# Custom exception — carries an error code and a human message
# ─────────────────────────────────────────────────────────────────────────────

class UploadValidationError(Exception):
    """
    Raised by save_upload() when validation fails.

    Attributes:
        code:    Machine-readable error code (e.g. "NO_FILE", "INVALID_TYPE").
        message: Human-readable explanation to surface in the API response.
        status:  Suggested HTTP status code for the endpoint to use.
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
class UploadResult:
    """
    Returned by save_upload() on success.
    Converted to a dict and passed into success_response(data=...).
    """
    file_id:       str   # UUID assigned to this upload
    original_name: str   # original filename submitted by the client
    saved_name:    str   # actual filename on disk (UUID-prefixed, sanitised)
    extension:     str   # lowercase extension without the dot, e.g. "csv"
    size_bytes:    int   # exact file size in bytes
    upload_path:   str   # relative path where the file was saved

    def to_dict(self) -> dict:
        return {
            "file_id":       self.file_id,
            "original_name": self.original_name,
            "saved_name":    self.saved_name,
            "extension":     self.extension,
            "size_bytes":    self.size_bytes,
            "upload_path":   self.upload_path,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Core service function
# ─────────────────────────────────────────────────────────────────────────────

def save_upload(
    file: FileStorage,
    upload_dir: str,
    allowed_extensions: set,
    max_bytes: int,
) -> UploadResult:
    """
    Validate and persist an uploaded file.

    Steps
    ──────
    1. Check a file object was actually supplied.
    2. Check the filename is not empty.
    3. Extract and validate the file extension.
    4. Sanitise the filename (werkzeug.utils.secure_filename).
    5. Read content into memory and check file size.
    6. Build a UUID-prefixed unique filename and save to upload_dir.
    7. Return an UploadResult with all metadata.

    Args:
        file:               werkzeug FileStorage object from request.files.
        upload_dir:         Directory path to save the file (created if absent).
        allowed_extensions: Set of lowercase extensions without dots {"csv","pdf"}.
        max_bytes:          Maximum allowed file size in bytes.

    Returns:
        UploadResult on success.

    Raises:
        UploadValidationError on any validation failure.
    """

    # ── 1. File object presence ───────────────────────────────────────────────
    if file is None:
        raise UploadValidationError(
            code="NO_FILE",
            message="No file was included in the request. "
                    "Send the file under the form-data key 'file'.",
        )

    # ── 2. Filename presence ──────────────────────────────────────────────────
    original_name = (file.filename or "").strip()
    if not original_name:
        raise UploadValidationError(
            code="EMPTY_FILENAME",
            message="The uploaded file has no filename.",
        )

    # ── 3. Extension validation ───────────────────────────────────────────────
    extension = _extract_extension(original_name)
    if not extension:
        raise UploadValidationError(
            code="NO_EXTENSION",
            message=(
                f"'{original_name}' has no file extension. "
                f"Accepted formats: {_format_allowed(allowed_extensions)}."
            ),
        )
    if extension not in allowed_extensions:
        raise UploadValidationError(
            code="INVALID_EXTENSION",
            message=(
                f"'.{extension}' files are not supported. "
                f"Accepted formats: {_format_allowed(allowed_extensions)}."
            ),
        )

    # ── 4. Sanitise filename ──────────────────────────────────────────────────
    safe_name = secure_filename(original_name)
    if not safe_name:
        # secure_filename can return "" for names made entirely of special chars
        safe_name = f"upload.{extension}"

    # ── 5. Size validation ────────────────────────────────────────────────────
    content = file.read()
    size_bytes = len(content)

    if size_bytes == 0:
        raise UploadValidationError(
            code="EMPTY_FILE",
            message="The uploaded file is empty (0 bytes).",
        )

    if size_bytes > max_bytes:
        max_mb = max_bytes / (1024 * 1024)
        actual_mb = size_bytes / (1024 * 1024)
        raise UploadValidationError(
            code="FILE_TOO_LARGE",
            message=(
                f"File size {actual_mb:.2f} MB exceeds the "
                f"maximum allowed size of {max_mb:.0f} MB."
            ),
            status=413,
        )

    # ── 6. Save to disk ───────────────────────────────────────────────────────
    file_id   = str(uuid.uuid4())
    saved_name = f"{file_id}_{safe_name}"

    dest_dir = Path(upload_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest_path = dest_dir / saved_name
    dest_path.write_bytes(content)

    logger.info(
        "File saved | id=%s | original='%s' | size=%d bytes | path='%s'",
        file_id, original_name, size_bytes, dest_path,
    )

    # ── 7. Return result ──────────────────────────────────────────────────────
    return UploadResult(
        file_id       = file_id,
        original_name = original_name,
        saved_name    = saved_name,
        extension     = extension,
        size_bytes    = size_bytes,
        upload_path   = str(dest_path),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────────────────────

def _extract_extension(filename: str) -> str:
    """
    Return the lowercase extension of filename without the leading dot.
    Returns "" if the filename has no extension or ends with a dot.

    Examples:
        "report.CSV"  → "csv"
        "data.tar.gz" → "gz"   (only the last segment)
        "README"      → ""
    """
    _, dot_ext = os.path.splitext(filename)
    return dot_ext.lstrip(".").lower()


def _format_allowed(extensions: set) -> str:
    """Return a sorted, human-readable list of accepted extensions."""
    return ", ".join(f".{e}" for e in sorted(extensions))