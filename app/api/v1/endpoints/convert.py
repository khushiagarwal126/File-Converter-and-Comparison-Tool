"""
app/api/v1/endpoints/convert.py
────────────────────────────────
File conversion Blueprint.

Rules for this layer:
  - HTTP concerns only: parse the request, call the service, return a response.
  - No conversion logic here — that lives entirely in converter_service.py.
  - Catch ConversionError from the service and map it to error_response().
"""

from flask import Blueprint, request

from app.core.config import settings
from app.utils.logger import logger
from app.utils.response import success_response, error_response
from app.services.converter_service import convert_file, ConversionError

convert_bp = Blueprint("convert", __name__)


@convert_bp.post("/convert")
def convert():
    """
    POST /api/v1/convert

    Convert a previously uploaded file to a different format.
    The source file must already exist in UPLOAD_DIR (saved by POST /upload).

    Request body (JSON):
        {
            "file_path":     "uploads/temp/<uuid>_filename.csv",
            "target_format": "json"
        }

    Response 200 — conversion succeeded:
        {
            "success": true,
            "message": "File converted successfully",
            "data": {
                "conversion_id":   "a1b2…",
                "source_path":     "uploads/temp/…csv",
                "output_path":     "outputs/…json",
                "source_format":   "csv",
                "target_format":   "json",
                "source_filename": "uuid_report.csv",
                "output_filename": "uuid_report.json",
                "size_bytes":      1024
            }
        }

    Error responses (400 / 404 / 500):
        { "success": false, "error": "CODE", "message": "…" }

    Error codes:
        MISSING_BODY          – request is not JSON or body is empty
        MISSING_FILE_PATH     – "file_path" key absent from body
        MISSING_TARGET_FORMAT – "target_format" key absent from body
        FILE_NOT_FOUND        – file_path does not exist on disk
        UNSUPPORTED_SOURCE_FORMAT / UNSUPPORTED_TARGET_FORMAT
        SAME_FORMAT           – source and target are identical
        CONVERSION_FAILED     – unexpected error during conversion
    """
    # ── Parse JSON body ───────────────────────────────────────────────────────
    body = request.get_json(silent=True)
    if not body:
        return error_response(
            "MISSING_BODY",
            "Request body must be JSON with 'file_path' and 'target_format'.",
        )

    file_path     = (body.get("file_path") or "").strip()
    target_format = (body.get("target_format") or "").strip().lower()

    if not file_path:
        return error_response(
            "MISSING_FILE_PATH",
            "Request body must include 'file_path' "
            "(the upload_path returned by POST /api/v1/upload).",
        )
    if not target_format:
        return error_response(
            "MISSING_TARGET_FORMAT",
            "Request body must include 'target_format' "
            "(e.g. 'json', 'csv', 'xlsx', 'xml', 'txt', 'pdf').",
        )

    logger.info(
        "Convert request | file_path='%s' | target_format='%s'",
        file_path, target_format,
    )

    # ── Delegate to service ───────────────────────────────────────────────────
    try:
        result = convert_file(
            source_path   = file_path,
            target_format = target_format,
            output_dir    = settings.OUTPUT_DIR,
        )
    except ConversionError as exc:
        logger.warning(
            "Conversion rejected | code=%s | %s", exc.code, exc.message
        )
        return error_response(exc.code, exc.message, status=exc.status)

    return success_response(
        data    = result.to_dict(),
        message = "File converted successfully",
        status  = 200,
    )