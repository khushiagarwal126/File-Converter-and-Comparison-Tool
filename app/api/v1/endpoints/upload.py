"""
app/api/v1/endpoints/upload.py
───────────────────────────────
File upload Blueprint.

Rules for this layer:
  - HTTP concerns only: parse the request, delegate to the service, build the response.
  - No file I/O or validation logic here — that lives in upload_service.py.
  - Catch UploadValidationError from the service and map it to error_response().
"""

from flask import Blueprint, request
from app.core.config import settings
from app.utils.logger import logger
from app.utils.response import success_response, error_response
from app.services.upload_service import save_upload, UploadValidationError

upload_bp = Blueprint("upload", __name__)


@upload_bp.post("/upload")
def upload_file():
    """
    POST /api/v1/upload

    Accept a single file via multipart/form-data under the key ``file``.
    Validates extension and size, then saves the file to UPLOAD_DIR.
    Does not perform any conversion — that is a separate step.

    Request
    ────────
    Content-Type: multipart/form-data
    Body field:   file=<binary>

    Response 201 — file accepted and saved:
        {
            "success": true,
            "message": "File uploaded successfully",
            "data": {
                "file_id":       "3f2a…",
                "original_name": "report.csv",
                "saved_name":    "3f2a…_report.csv",
                "extension":     "csv",
                "size_bytes":    2048,
                "upload_path":   "uploads/temp/3f2a…_report.csv"
            }
        }

    Response 400 — missing file, empty filename, bad extension, empty file:
        { "success": false, "error": "INVALID_EXTENSION", "message": "…" }

    Response 413 — file exceeds MAX_UPLOAD_SIZE_MB:
        { "success": false, "error": "FILE_TOO_LARGE", "message": "…" }
    """
    logger.info("Upload request received | content-length=%s",
                request.content_length)

    # Pull the file out of the multipart payload.
    # request.files returns an empty ImmutableMultiDict when no file is sent,
    # so .get() safely returns None without raising.
    file = request.files.get("file")

    try:
        result = save_upload(
            file               = file,
            upload_dir         = settings.UPLOAD_DIR,
            allowed_extensions = settings.ALLOWED_EXTENSIONS,
            max_bytes          = settings.MAX_UPLOAD_SIZE_BYTES,
        )
    except UploadValidationError as exc:
        logger.warning("Upload rejected | code=%s | %s", exc.code, exc.message)
        return error_response(exc.code, exc.message, status=exc.status)

    return success_response(
        data    = result.to_dict(),
        message = "File uploaded successfully",
        status  = 201,
    )