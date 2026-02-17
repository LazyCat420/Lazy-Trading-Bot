"""Structured logging for the trading bot."""

import logging
import sys

from app.config import settings


def _setup_logger(name: str = "lazy_trader") -> logging.Logger:
    """Create a logger that writes to both console and file."""
    log = logging.getLogger(name)
    log.setLevel(logging.DEBUG)

    # Prevent duplicate handlers on reimport
    if log.handlers:
        return log

    fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)-8s %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler (INFO+)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    log.addHandler(console)

    # File handler (DEBUG+)
    log_file = settings.LOGS_DIR / "trading_bot.log"
    file_h = logging.FileHandler(log_file, encoding="utf-8")
    file_h.setLevel(logging.DEBUG)
    file_h.setFormatter(fmt)
    log.addHandler(file_h)

    return log


logger = _setup_logger()
