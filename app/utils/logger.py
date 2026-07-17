"""
app/utils/logger.py
───────────────────
Centralised logging configuration.

Import `logger` from this module everywhere in the project.
Never call logging.getLogger() directly in business logic files.

Usage:
    from app.utils.logger import logger
    logger.info("Something happened")
    logger.warning("Worth noting: %s", detail)
    logger.error("Failed: %s", error, exc_info=True)
"""

import logging
import os
import sys
from pathlib import Path


def _build_logger() -> logging.Logger:
    """
    Build and return the application logger.
    Called once at module import — result is cached in `logger` below.
    """
    log_level_name = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    log = logging.getLogger("file_converter")
    log.setLevel(log_level)

    # Guard: skip if handlers already attached (e.g. during parallel test runs)
    if log.handlers:
        return log

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── Console handler (stdout) ──────────────────────────────────────────────
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    log.addHandler(console)

    # ── File handler — writes to logs/app.log ─────────────────────────────────
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    fh = logging.FileHandler(log_dir / "app.log", encoding="utf-8")
    fh.setFormatter(formatter)
    log.addHandler(fh)

    return log


# Module-level singleton — import this name throughout the project
logger = _build_logger()