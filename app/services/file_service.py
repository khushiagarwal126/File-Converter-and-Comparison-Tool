"""
app/services/file_service.py
─────────────────────────────
Business logic for file listing, metadata retrieval, download, and deletion.

Rules for service files:
  - No Flask imports. No request/response objects.
  - Pure Python: accept plain arguments, return dataclasses or raise.
  - All HTTP concerns live in the endpoint layer (files.py).

File identity model
────────────────────
    Both upload and conversion use UUID-prefixed filenames:

      Upload:   <file_id>_<original_name>.<ext>
      Convert:  <conversion_id>_<upload_file_id>_<original_name>.<ext>

    The leading UUID segment IS the file_id used in all API routes.
    Parsing is done with _parse_file_id() which splits on the first "_"
    and validates that the result is a well-formed UUID4.

    Directories scanned:
        - settings.UPLOAD_DIR  → "uploaded" files
        - settings.OUTPUT_DIR  → "converted" files

Security
─────────
    _resolve_safe_path() resolves the requested path and confirms it
    sits inside the permitted directory (UPLOAD_DIR or OUTPUT_DIR).
    Any path component that escapes the root (../../etc/passwd) raises
    FileServiceError with code PATH_TRAVERSAL before any I/O occurs.

Public interface
────────────────
    file_list  = list_files(upload_dir, output_dir)
    metadata   = get_file(file_id, upload_dir, output_dir)
    (path, fn) = get_download(file_id, upload_dir, output_dir)
    deleted    = delete_file(file_id, upload_dir, output_dir)
"""

import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from app.utils.logger import logger


# ─────────────────────────────────────────────────────────────────────────────
# Custom exception
# ─────────────────────────────────────────────────────────────────────────────

class FileServiceError(Exception):
    """
    Raised by any public function in this module on failure.

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
class FileMetadata:
    """Metadata for a single managed file."""

    file_id:       str           # leading UUID from the filename
    filename:      str           # full filename on disk
    original_name: str           # filename with the UUID prefix stripped
    extension:     str           # lowercase extension without dot
    file_type:     str           # "uploaded" | "converted"
    size_bytes:    int           # file size in bytes
    path:          str           # absolute path on disk
    created_at:    str           # ISO 8601 UTC timestamp of file creation

    def to_dict(self) -> dict:
        return {
            "file_id":       self.file_id,
            "filename":      self.filename,
            "original_name": self.original_name,
            "extension":     self.extension,
            "file_type":     self.file_type,
            "size_bytes":    self.size_bytes,
            "path":          self.path,
            "created_at":    self.created_at,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def list_files(upload_dir: str, output_dir: str) -> List[FileMetadata]:
    """
    Scan both managed directories and return metadata for every file found.

    Files are returned sorted by creation time, newest first.
    Hidden files (starting with ".") and directories are skipped.

    Args:
        upload_dir: Directory containing uploaded files.
        output_dir: Directory containing converted output files.

    Returns:
        List of FileMetadata objects (may be empty if no files exist yet).
    """
    results: List[FileMetadata] = []

    for directory, file_type in [
        (upload_dir, "uploaded"),
        (output_dir, "converted"),
    ]:
        dir_path = Path(directory)
        if not dir_path.exists():
            continue
        for entry in dir_path.iterdir():
            if not entry.is_file() or entry.name.startswith("."):
                continue
            meta = _build_metadata(entry, file_type)
            if meta:                       # None if filename can't be parsed
                results.append(meta)

    # Newest first
    results.sort(key=lambda m: m.created_at, reverse=True)
    logger.info("list_files | found %d files", len(results))
    return results


def get_file(
    file_id: str,
    upload_dir: str,
    output_dir: str,
) -> FileMetadata:
    """
    Return metadata for the file whose name starts with *file_id*.

    Searches upload_dir first, then output_dir.

    Args:
        file_id:    UUID string to look up.
        upload_dir: Directory containing uploaded files.
        output_dir: Directory containing converted output files.

    Returns:
        FileMetadata for the matching file.

    Raises:
        FileServiceError(FILE_NOT_FOUND, 404) if no match is found.
        FileServiceError(INVALID_FILE_ID, 400) if file_id is malformed.
    """
    _validate_file_id(file_id)
    entry, file_type = _find_file(file_id, upload_dir, output_dir)
    meta = _build_metadata(entry, file_type)
    if meta is None:
        raise FileServiceError(
            "FILE_NOT_FOUND",
            f"No file with id '{file_id}' was found.",
            status=404,
        )
    logger.info("get_file | id=%s | path=%s", file_id, entry)
    return meta


def get_download(
    file_id: str,
    upload_dir: str,
    output_dir: str,
) -> Tuple[str, str]:
    """
    Resolve the absolute file path and safe download filename for *file_id*.

    Performs a path-traversal check before returning.

    Args:
        file_id:    UUID string to look up.
        upload_dir: Directory containing uploaded files.
        output_dir: Directory containing converted output files.

    Returns:
        Tuple of (absolute_path: str, download_filename: str).

    Raises:
        FileServiceError on validation failure or file not found.
    """
    _validate_file_id(file_id)
    entry, file_type = _find_file(file_id, upload_dir, output_dir)
    root = upload_dir if file_type == "uploaded" else output_dir
    safe_path = _resolve_safe_path(entry, root)
    download_name = _original_name(entry.name)
    logger.info(
        "get_download | id=%s | path=%s | download_as=%s",
        file_id, safe_path, download_name,
    )
    return safe_path, download_name


def delete_file(
    file_id: str,
    upload_dir: str,
    output_dir: str,
) -> dict:
    """
    Delete the file whose name starts with *file_id* from disk.

    Args:
        file_id:    UUID string to look up.
        upload_dir: Directory containing uploaded files.
        output_dir: Directory containing converted output files.

    Returns:
        Dict with deletion confirmation metadata.

    Raises:
        FileServiceError on validation failure or file not found.
    """
    _validate_file_id(file_id)
    entry, file_type = _find_file(file_id, upload_dir, output_dir)
    root = upload_dir if file_type == "uploaded" else output_dir
    safe_path = _resolve_safe_path(entry, root)

    file_path = Path(safe_path)
    file_path.unlink()

    logger.info(
        "delete_file | id=%s | deleted=%s | type=%s",
        file_id, entry.name, file_type,
    )
    return {
        "file_id":  file_id,
        "filename": entry.name,
        "file_type": file_type,
        "deleted":  True,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _validate_file_id(file_id: str) -> None:
    """
    Raise FileServiceError(INVALID_FILE_ID) if *file_id* is not a valid UUID4.
    This prevents arbitrary strings (including path components) being used
    as identifiers before any filesystem access occurs.
    """
    if not file_id or not isinstance(file_id, str):
        raise FileServiceError(
            "INVALID_FILE_ID",
            "file_id must be a non-empty string.",
        )
    try:
        val = uuid.UUID(file_id, version=4)
        if str(val) != file_id.lower():
            raise ValueError()
    except (ValueError, AttributeError):
        raise FileServiceError(
            "INVALID_FILE_ID",
            f"'{file_id}' is not a valid UUID4 file identifier.",
        )


def _find_file(
    file_id: str,
    upload_dir: str,
    output_dir: str,
) -> Tuple[Path, str]:
    """
    Search both directories for a file whose name starts with *file_id*.
    Returns (Path, file_type) for the first match found.
    upload_dir is searched before output_dir.

    Raises FileServiceError(FILE_NOT_FOUND, 404) if nothing matches.
    """
    for directory, file_type in [
        (upload_dir, "uploaded"),
        (output_dir, "converted"),
    ]:
        dir_path = Path(directory)
        if not dir_path.exists():
            continue
        for entry in dir_path.iterdir():
            if entry.is_file() and entry.name.startswith(file_id):
                return entry, file_type

    raise FileServiceError(
        "FILE_NOT_FOUND",
        f"No file with id '{file_id}' was found. "
        "It may have been deleted or may not have been uploaded yet.",
        status=404,
    )


def _resolve_safe_path(entry: Path, root_dir: str) -> str:
    """
    Resolve *entry* to an absolute path and confirm it sits inside *root_dir*.

    This is the primary path-traversal guard. Even if a filename somehow
    contained ".." segments, the resolved absolute path check catches it.

    Raises:
        FileServiceError(PATH_TRAVERSAL, 400) if the resolved path escapes
        the root directory.
    """
    resolved     = entry.resolve()
    resolved_root = Path(root_dir).resolve()

    try:
        resolved.relative_to(resolved_root)
    except ValueError:
        logger.warning(
            "Path traversal attempt blocked | path=%s | root=%s",
            resolved, resolved_root,
        )
        raise FileServiceError(
            "PATH_TRAVERSAL",
            "Access denied: the requested path is outside the permitted directory.",
            status=400,
        )

    return str(resolved)


def _build_metadata(entry: Path, file_type: str) -> Optional[FileMetadata]:
    """
    Build a FileMetadata object from a filesystem entry.

    Returns None if the filename cannot be parsed as a UUID-prefixed name
    (so stray files like .gitkeep don't cause errors).
    """
    file_id = _parse_file_id(entry.name)
    if file_id is None:
        return None

    try:
        stat       = entry.stat()
        size_bytes = stat.st_size
        created_at = datetime.fromtimestamp(
            stat.st_ctime, tz=timezone.utc
        ).isoformat()
    except OSError:
        return None

    ext           = entry.suffix.lstrip(".").lower()
    original_name = _original_name(entry.name)

    return FileMetadata(
        file_id       = file_id,
        filename      = entry.name,
        original_name = original_name,
        extension     = ext,
        file_type     = file_type,
        size_bytes    = size_bytes,
        path          = str(entry.resolve()),
        created_at    = created_at,
    )


def _parse_file_id(filename: str) -> Optional[str]:
    """
    Extract and validate the leading UUID4 from a filename.

    Expected formats:
        <uuid>_<rest>.<ext>          (uploads)
        <uuid>_<uuid>_<rest>.<ext>   (converted — only the FIRST uuid is used)

    Returns the UUID string if valid, None otherwise.
    """
    # UUID4 is exactly 36 characters: 8-4-4-4-12
    if len(filename) < 37 or filename[36] != "_":
        return None
    candidate = filename[:36]
    try:
        val = uuid.UUID(candidate, version=4)
        return str(val)          # normalised lowercase
    except (ValueError, AttributeError):
        return None


def _original_name(filename: str) -> str:
    """
    Strip the leading UUID prefix from a filename.

    "<uuid>_report.csv"           → "report.csv"
    "<uuid>_<uuid>_report.json"   → "<uuid>_report.json"  (one prefix only)
    """
    if len(filename) > 37 and filename[36] == "_":
        return filename[37:]
    return filename