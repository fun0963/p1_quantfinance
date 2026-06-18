"""Centralized logging via loguru.

Call `setup_logging()` once at the entrypoint; use `get_logger(__name__)`
elsewhere. Logs go to stderr (human-readable) and a rotating file under LOG_DIR.
"""
from __future__ import annotations

import sys

from loguru import logger

_CONFIGURED = False


def setup_logging(level: str = "INFO", log_dir=None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    )
    if log_dir is not None:
        logger.add(
            f"{log_dir}/quant_{{time:YYYY-MM-DD}}.log",
            level=level,
            rotation="00:00",          # new file daily
            retention="30 days",
            encoding="utf-8",
        )
    _CONFIGURED = True


def get_logger(name: str):
    """Return a logger bound to the given module name."""
    return logger.bind(name=name)
