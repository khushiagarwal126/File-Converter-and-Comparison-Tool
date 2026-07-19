"""
app/api/v1/endpoints/compare.py
────────────────────────────────
File comparison Blueprint.

Rules for this layer:
  - HTTP concerns only: parse the request, call the service, return a response.
  - No comparison logic here — that lives entirely in comparison_service.py.
  - Catch ComparisonError and map it to error_response().
"""

from flask import Blueprint, request

from app.utils.logger import logger
from app.utils.response import success_response, error_response
from app.services.comparison_service import compare_files, ComparisonError

compare_bp = Blueprint("compare", __name__)


@compare_bp.post("/compare")
def compare():
    """
    POST /api/v1/compare

    Compare two previously uploaded files of the same format.
    Both files must already exist on disk (saved by POST /api/v1/upload).

    Request body (JSON):
        {
            "file_a_path": "uploads/temp/<uuid>_file1.csv",
            "file_b_path": "uploads/temp/<uuid>_file2.csv"
        }

    Response 200 — comparison succeeded:
        {
            "success": true,
            "message": "Files compared successfully",
            "data": {
                "comparison_id":         "a1b2…",
                "file_a":                "/abs/path/to/file1.csv",
                "file_b":                "/abs/path/to/file2.csv",
                "format":                "csv",
                "files_match":           false,
                "similarity_percentage": 85.71,
                "total_differences":     3,
                "summary":               "Files 'file1.csv' and 'file2.csv' differ…",
                "differences":           [ { "type": "CHANGED", "row": 2, ... } ],
                "compared_at":           "2026-07-18T17:00:00+00:00"
            }
        }

    Error codes:
        MISSING_BODY         – request is not JSON or body is absent
        MISSING_FILE_A_PATH  – "file_a_path" key absent
        MISSING_FILE_B_PATH  – "file_b_path" key absent
        FILE_A_NOT_FOUND     – file_a_path does not exist on disk
        FILE_B_NOT_FOUND     – file_b_path does not exist on disk
        UNSUPPORTED_FORMAT_A / UNSUPPORTED_FORMAT_B
        FORMAT_MISMATCH      – the two files have different extensions
        COMPARISON_FAILED    – unexpected error during comparison
    """
    # ── Parse JSON body ───────────────────────────────────────────────────────
    body = request.get_json(silent=True)
    if not body:
        return error_response(
            "MISSING_BODY",
            "Request body must be JSON with 'file_a_path' and 'file_b_path'.",
        )

    file_a_path = (body.get("file_a_path") or "").strip()
    file_b_path = (body.get("file_b_path") or "").strip()

    if not file_a_path:
        return error_response(
            "MISSING_FILE_A_PATH",
            "Request body must include 'file_a_path' "
            "(the upload_path returned by POST /api/v1/upload).",
        )
    if not file_b_path:
        return error_response(
            "MISSING_FILE_B_PATH",
            "Request body must include 'file_b_path' "
            "(the upload_path returned by POST /api/v1/upload).",
        )

    logger.info(
        "Compare request | file_a='%s' | file_b='%s'",
        file_a_path, file_b_path,
    )

    # ── Delegate to service ───────────────────────────────────────────────────
    try:
        result = compare_files(
            file_a_path=file_a_path,
            file_b_path=file_b_path,
        )
    except ComparisonError as exc:
        logger.warning(
            "Comparison rejected | code=%s | %s", exc.code, exc.message
        )
        return error_response(exc.code, exc.message, status=exc.status)

    return success_response(
        data    = result.to_dict(),
        message = "Files compared successfully",
        status  = 200,
    )