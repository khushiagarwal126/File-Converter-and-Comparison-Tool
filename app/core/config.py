"""
app/core/config.py
──────────────────
Central configuration for the entire application.

All values are read from the .env file via python-dotenv.
Import `settings` from this module — never read os.environ directly
anywhere else in the codebase.

Usage:
    from app.core.config import settings
    print(settings.ALLOWED_EXTENSIONS)
"""

import os
from pathlib import Path
from dotenv import load_dotenv

_BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(_BASE_DIR / ".env")


class Settings:
    # ── Application identity ──────────────────────────────────────────────────
    APP_NAME: str    = os.getenv("APP_NAME", "File Converter and Comparison Tool")
    APP_VERSION: str = os.getenv("APP_VERSION", "1.0.0")
    APP_ENV: str     = os.getenv("APP_ENV", "development")
    DEBUG: bool      = os.getenv("DEBUG", "True").strip().lower() == "true"

    # ── API ───────────────────────────────────────────────────────────────────
    API_PREFIX: str = os.getenv("API_PREFIX", "/api/v1")

    # ── CORS ──────────────────────────────────────────────────────────────────
    ALLOWED_ORIGINS: list = [
        o.strip()
        for o in os.getenv(
            "ALLOWED_ORIGINS",
            "http://localhost:3000,http://localhost:5173",
        ).split(",")
        if o.strip()
    ]

    # ── File handling ─────────────────────────────────────────────────────────
    UPLOAD_DIR: str         = os.getenv("UPLOAD_DIR", "uploads/temp")
    OUTPUT_DIR: str         = os.getenv("OUTPUT_DIR", "outputs")
    MAX_UPLOAD_SIZE_MB: int = int(os.getenv("MAX_UPLOAD_SIZE_MB", "50"))

    # Allowed file extensions (lowercase, without the leading dot).
    # Every upload is validated against this set before being saved.
    ALLOWED_EXTENSIONS: set = {"csv", "xlsx", "json", "xml", "txt", "pdf"}

    # ── Logging ───────────────────────────────────────────────────────────────
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # ── Computed helpers ──────────────────────────────────────────────────────
    @property
    def MAX_UPLOAD_SIZE_BYTES(self) -> int:
        """Max upload size in bytes — used by Flask MAX_CONTENT_LENGTH."""
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024

    @property
    def IS_PRODUCTION(self) -> bool:
        return self.APP_ENV.strip().lower() == "production"


settings = Settings()