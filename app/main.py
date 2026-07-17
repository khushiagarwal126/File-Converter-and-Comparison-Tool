"""
app/main.py
───────────
Application factory and WSGI entry point.

Sole responsibilities of this file:
  1. create_app() — build and configure the Flask application instance.
  2. Register CORS (after_request hook + OPTIONS preflight handler).
  3. Mount the versioned API Blueprint router.
  4. Register global JSON error handlers.
  5. Expose the module-level `app` object consumed by run.py and gunicorn.

Keep this file thin. No business logic, no route handlers (except GET /).
"""

from pathlib import Path
from flask import Flask, jsonify, request, make_response

from app.core.config import settings
from app.utils.logger import logger
from app.api.v1.router import register_blueprints


# ─────────────────────────────────────────────────────────────────────────────
# Application factory
# ─────────────────────────────────────────────────────────────────────────────

def create_app() -> Flask:
    """
    Build, configure, and return the Flask application.

    Using a factory instead of a bare global `app = Flask(__name__)` means:
      - Each test can call create_app() for a clean, isolated instance.
      - Configuration can vary per environment without import-time side-effects.
    """
    app = Flask(__name__)

    # ── Flask built-in config ─────────────────────────────────────────────────
    app.config.update(
        DEBUG=settings.DEBUG,
        TESTING=False,
        # Flask enforces this automatically and returns 413 if exceeded.
        MAX_CONTENT_LENGTH=settings.MAX_UPLOAD_SIZE_BYTES,
        # Preserve insertion order in all jsonify() calls.
        JSON_SORT_KEYS=False,
    )

    # ── Ensure runtime directories exist ──────────────────────────────────────
    for d in [settings.UPLOAD_DIR, settings.OUTPUT_DIR, "logs"]:
        Path(d).mkdir(parents=True, exist_ok=True)

    # ── Middleware ────────────────────────────────────────────────────────────
    _register_cors(app)

    # ── API Blueprints ────────────────────────────────────────────────────────
    register_blueprints(app, prefix=settings.API_PREFIX)

    # ── Root route ────────────────────────────────────────────────────────────
    @app.get("/")
    def root():
        """
        GET /

        Landing route — confirms the API is reachable and points to health.
        Not versioned; intentionally kept outside the /api/v1 prefix.
        """
        return jsonify({
            "success": True,
            "message": f"Welcome to {settings.APP_NAME}",
            "version": settings.APP_VERSION,
            "health":  f"{settings.API_PREFIX}/health",
        }), 200

    # ── Global error handlers ─────────────────────────────────────────────────
    _register_error_handlers(app)

    logger.info(
        "App created — %s v%s [%s]",
        settings.APP_NAME,
        settings.APP_VERSION,
        settings.APP_ENV,
    )

    return app


# ─────────────────────────────────────────────────────────────────────────────
# CORS
# ─────────────────────────────────────────────────────────────────────────────

def _register_cors(app: Flask) -> None:
    """
    Attach CORS headers to every outgoing response.
    Handle OPTIONS preflight requests before they reach any route.

    Allowed origins are read from settings.ALLOWED_ORIGINS (set in .env).
    """
    allowed_origins = set(settings.ALLOWED_ORIGINS)

    @app.before_request
    def handle_preflight():
        """Return 204 immediately for all CORS preflight requests."""
        if request.method == "OPTIONS":
            resp = make_response("", 204)
            _set_cors_headers(resp, allowed_origins)
            return resp

    @app.after_request
    def add_cors_headers(response):
        """Attach CORS headers to every response, including errors."""
        _set_cors_headers(response, allowed_origins)
        return response


def _set_cors_headers(response, allowed_origins: set) -> None:
    """Write the four CORS headers onto a response object in-place."""
    origin = request.headers.get("Origin", "")
    response.headers["Access-Control-Allow-Origin"] = (
        origin if origin in allowed_origins else ", ".join(allowed_origins)
    )
    response.headers["Access-Control-Allow-Headers"] = (
        "Content-Type, Authorization"
    )
    response.headers["Access-Control-Allow-Methods"] = (
        "GET, POST, PUT, DELETE, OPTIONS"
    )
    response.headers["Access-Control-Allow-Credentials"] = "true"


# ─────────────────────────────────────────────────────────────────────────────
# Global error handlers
# ─────────────────────────────────────────────────────────────────────────────

def _register_error_handlers(app: Flask) -> None:
    """
    Override Flask's default HTML error pages with JSON error envelopes.
    All handlers use the same shape as error_response() in utils/response.py.
    """

    @app.errorhandler(400)
    def bad_request(e):
        logger.warning("400 Bad Request | %s %s", request.method, request.path)
        return jsonify({
            "success": False,
            "error":   "BAD_REQUEST",
            "message": str(e),
        }), 400

    @app.errorhandler(404)
    def not_found(e):
        logger.warning("404 Not Found | %s %s", request.method, request.path)
        return jsonify({
            "success": False,
            "error":   "NOT_FOUND",
            "message": "The requested resource was not found.",
        }), 404

    @app.errorhandler(405)
    def method_not_allowed(e):
        logger.warning(
            "405 Method Not Allowed | %s %s", request.method, request.path
        )
        return jsonify({
            "success": False,
            "error":   "METHOD_NOT_ALLOWED",
            "message": str(e),
        }), 405

    @app.errorhandler(413)
    def payload_too_large(e):
        logger.warning("413 Payload Too Large | %s", request.path)
        return jsonify({
            "success": False,
            "error":   "FILE_TOO_LARGE",
            "message": (
                f"Upload exceeds the maximum allowed size "
                f"of {settings.MAX_UPLOAD_SIZE_MB} MB."
            ),
        }), 413

    @app.errorhandler(500)
    def internal_server_error(e):
        logger.error(
            "500 Internal Server Error | %s %s | %s",
            request.method, request.path, str(e),
            exc_info=True,
        )
        return jsonify({
            "success": False,
            "error":   "INTERNAL_SERVER_ERROR",
            "message": "An unexpected error occurred. Please try again later.",
        }), 500


# ─────────────────────────────────────────────────────────────────────────────
# Module-level app instance
# Used by:  run.py  |  flask --app app.main:app run  |  gunicorn app.main:app
# ─────────────────────────────────────────────────────────────────────────────
app = create_app()