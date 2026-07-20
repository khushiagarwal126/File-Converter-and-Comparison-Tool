"""
app/api/v1/endpoints/files.py
──────────────────────────────
File management Blueprint.

Provides:
    GET    /api/v1/files                     list all managed files
    GET    /api/v1/files/<file_id>           get metadata for one file
    GET    /api/v1/files/<file_id>/download  stream the file to the client
    DELETE /api/v1/files/<file_id>           delete a file from disk

Rules for this layer:
  - HTTP concerns only: routing, request parsing, response building.
  - No filesystem logic here — that lives in file_service.py.
  - Catch FileServiceError and map it to error_response().
"""

from flask import Blueprint, send_file

from app.core.config import settings
from app.utils.logger import logger
from app.utils.response import success_response, error_response
from app.services.file_service import (
    list_files,
    get_file,
    get_download,
    delete_file,
    FileServiceError,
)

files_bp = Blueprint("files", __name__)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/files
# ─────────────────────────────────────────────────────────────────────────────

@files_bp.get("/files")
def list_all_files():
    """
    GET /api/v1/files

    List all uploaded and converted files with their metadata.
    Files are returned newest first.

    Response 200:
        {
            "success": true,
            "message": "3 file(s) found",
            "data": {
                "count": 3,
                "files": [
                    {
                        "file_id":       "3f2a…",
                        "filename":      "3f2a…_report.csv",
                        "original_name": "report.csv",
                        "extension":     "csv",
                        "file_type":     "uploaded",
                        "size_bytes":    1024,
                        "path":          "/abs/path/uploads/temp/…",
                        "created_at":    "2026-07-20T10:00:00+00:00"
                    },
                    …
                ]
            }
        }
    """
    logger.info("List files requested")

    files = list_files(
        upload_dir = settings.UPLOAD_DIR,
        output_dir = settings.OUTPUT_DIR,
    )

    file_dicts = [f.to_dict() for f in files]
    count      = len(file_dicts)

    return success_response(
        data    = {"count": count, "files": file_dicts},
        message = f"{count} file(s) found",
        status  = 200,
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/files/<file_id>
# ─────────────────────────────────────────────────────────────────────────────

@files_bp.get("/files/<file_id>")
def get_file_metadata(file_id: str):
    """
    GET /api/v1/files/<file_id>

    Return metadata for a single file identified by its UUID.

    Response 200:
        {
            "success": true,
            "message": "File found",
            "data": { <FileMetadata dict> }
        }

    Response 400: INVALID_FILE_ID
    Response 404: FILE_NOT_FOUND
    """
    logger.info("Get file metadata | id=%s", file_id)

    try:
        meta = get_file(
            file_id    = file_id,
            upload_dir = settings.UPLOAD_DIR,
            output_dir = settings.OUTPUT_DIR,
        )
    except FileServiceError as exc:
        logger.warning("get_file failed | code=%s | %s", exc.code, exc.message)
        return error_response(exc.code, exc.message, status=exc.status)

    return success_response(
        data    = meta.to_dict(),
        message = "File found",
        status  = 200,
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/files/<file_id>/download
# ─────────────────────────────────────────────────────────────────────────────

@files_bp.get("/files/<file_id>/download")
def download_file(file_id: str):
    """
    GET /api/v1/files/<file_id>/download

    Stream the file identified by *file_id* to the client.
    The Content-Disposition header is set to attachment so browsers
    trigger a file save dialog rather than rendering inline.

    On success, returns the raw file bytes (not the standard JSON envelope).
    All error responses continue to use the standard JSON envelope.

    Response 200: raw file bytes with Content-Disposition: attachment
    Response 400: INVALID_FILE_ID | PATH_TRAVERSAL
    Response 404: FILE_NOT_FOUND
    """
    logger.info("Download requested | id=%s", file_id)

    try:
        abs_path, download_name = get_download(
            file_id    = file_id,
            upload_dir = settings.UPLOAD_DIR,
            output_dir = settings.OUTPUT_DIR,
        )
    except FileServiceError as exc:
        logger.warning("download failed | code=%s | %s", exc.code, exc.message)
        return error_response(exc.code, exc.message, status=exc.status)

    return send_file(
        abs_path,
        as_attachment   = True,
        download_name   = download_name,
    )


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /api/v1/files/<file_id>
# ─────────────────────────────────────────────────────────────────────────────

@files_bp.delete("/files/<file_id>")
def delete_file_by_id(file_id: str):
    """
    DELETE /api/v1/files/<file_id>

    Permanently delete the file identified by *file_id* from disk.

    Response 200:
        {
            "success": true,
            "message": "File deleted successfully",
            "data": {
                "file_id":   "3f2a…",
                "filename":  "3f2a…_report.csv",
                "file_type": "uploaded",
                "deleted":   true
            }
        }

    Response 400: INVALID_FILE_ID | PATH_TRAVERSAL
    Response 404: FILE_NOT_FOUND
    """
    logger.info("Delete requested | id=%s", file_id)

    try:
        result = delete_file(
            file_id    = file_id,
            upload_dir = settings.UPLOAD_DIR,
            output_dir = settings.OUTPUT_DIR,
        )
    except FileServiceError as exc:
        logger.warning("delete failed | code=%s | %s", exc.code, exc.message)
        return error_response(exc.code, exc.message, status=exc.status)

    return success_response(
        data    = result,
        message = "File deleted successfully",
        status  = 200,
    )