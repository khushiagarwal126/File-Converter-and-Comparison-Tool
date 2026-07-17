"""
app/api/v1/router.py
─────────────────────
Master router for API version 1.

This is the single registration point for all v1 Blueprints.
app/main.py calls register_blueprints() from here — it never imports
individual endpoint modules directly.

To add a new endpoint group:
    1. Create  app/api/v1/endpoints/your_resource.py  with a Blueprint.
    2. Import the Blueprint here and add it to register_blueprints().
    3. That's it — main.py does not need to change.
"""

from flask import Flask
from app.api.v1.endpoints.health import health_bp

# Future Blueprint imports are added here as the project grows:
# from app.api.v1.endpoints.convert import convert_bp
# from app.api.v1.endpoints.compare import compare_bp


def register_blueprints(app: Flask, prefix: str) -> None:
    """
    Register all v1 Blueprints onto the Flask application.

    Args:
        app:    The Flask application instance (from create_app()).
        prefix: The API version prefix, e.g. "/api/v1" (from settings).
                Every Blueprint's routes are mounted under this prefix.
    """
    app.register_blueprint(health_bp, url_prefix=prefix)

    # Uncomment as each module is built:
    # app.register_blueprint(convert_bp, url_prefix=prefix)
    # app.register_blueprint(compare_bp, url_prefix=prefix)