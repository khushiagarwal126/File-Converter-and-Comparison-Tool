"""
run.py
──────
Development server entry point.

Usage:
    python run.py

For production, use a proper WSGI server instead:
    gunicorn "app.main:app" --workers 4 --bind 0.0.0.0:5000
"""

from app.main import app
from app.core.config import settings

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=settings.DEBUG,
        use_reloader=settings.DEBUG,
    )