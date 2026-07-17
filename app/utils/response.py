"""
app/utils/response.py
─────────────────────
Standardised JSON response helpers.

Every endpoint must use these helpers so that all API responses
share the same envelope shape — making frontend handling consistent
and error messages predictable.

Success envelope:
    {
        "success": true,
        "message": "Human-readable note",
        "data":    { ... }          ← dict, list, or null
    }

Error envelope:
    {
        "success": false,
        "error":   "MACHINE_CODE",  ← short, uppercase, underscore-separated
        "message": "Human-readable detail"
    }

Usage:
    from app.utils.response import success_response, error_response

    return success_response({"id": 1}, message="File converted", status=200)
    return error_response("NOT_FOUND", "No file with that ID", status=404)
"""

from typing import Any
from flask import jsonify


def success_response(
    data: Any = None,
    message: str = "Success",
    status: int = 200,
):
    """
    Return a standardised success JSON response.

    Args:
        data:    The payload (dict, list, or None).
        message: Optional human-readable note for the caller.
        status:  HTTP status code (default 200).

    Returns:
        A Flask (Response, int) tuple ready to be returned from any view.
    """
    return jsonify({
        "success": True,
        "message": message,
        "data":    data,
    }), status


def error_response(
    error: str,
    message: str,
    status: int = 400,
):
    """
    Return a standardised error JSON response.

    Args:
        error:   Short machine-readable code, e.g. "NOT_FOUND", "BAD_REQUEST".
        message: Human-readable explanation for the developer or user.
        status:  HTTP status code (default 400).

    Returns:
        A Flask (Response, int) tuple ready to be returned from any view.
    """
    return jsonify({
        "success": False,
        "error":   error,
        "message": message,
    }), status