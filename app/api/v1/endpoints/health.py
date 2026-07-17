"""
app/api/v1/endpoints/health.py
───────────────────────────────
Health-check Blueprint.

Rules for all endpoint files:
  - Handle HTTP concerns only: routing, request parsing, response building.
  - No business logic — that belongs in app/services/.
  - One Blueprint per logical resource (health / convert / compare / …).
"""

from flask import Blueprint
from app.core.config import settings
from app.utils.logger import logger
from app.utils.response import success_response

# Blueprint name must be unique across the whole application.
# url_prefix is set in app/api/v1/router.py — not here.
health_bp = Blueprint("health", __name__)


@health_bp.get("/health")
def health_check():
    """
    GET /api/v1/health

    Confirms the service is alive and returns key runtime metadata.
    Use this endpoint to verify the API is reachable before sending jobs.

    Response 200:
        {
            "success": true,
            "message": "Service is running",
            "data": {
                "status":      "ok",
                "app_name":    "File Converter and Comparison Tool",
                "version":     "1.0.0",
                "environment": "development"
            }
        }
    """
    logger.info("Health check requested")

    return success_response(
        data={
            "status":      "ok",
            "app_name":    settings.APP_NAME,
            "version":     settings.APP_VERSION,
            "environment": settings.APP_ENV,
        },
        message="Service is running",
        status=200,
    )