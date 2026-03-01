"""Structured logging for the trading bot.

Each server start creates a fresh timestamped log file.
``trading_bot.log`` always contains the current run.
Only the 10 most recent files are kept for logs AND reports.
"""

import logging
import sys
from datetime import datetime
from pathlib import Path

from app.config import settings

_MAX_FILES = 10  # Keep only this many files per category


def prune_old_files(directory: Path, pattern: str, keep: int = _MAX_FILES) -> int:
    """Delete oldest files matching *pattern* if count exceeds *keep*.

    Returns the number of files deleted.
    """
    files = sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime)
    excess = len(files) - keep
    deleted = 0
    if excess > 0:
        for old in files[:excess]:
            try:
                old.unlink()
                deleted += 1
            except OSError:
                pass
    return deleted


def _setup_logger(name: str = "lazy_trader") -> logging.Logger:
    """Create a logger that writes to both console and a fresh per-run file."""
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

    # ── Per-run timestamped log file ──
    logs_dir = settings.LOGS_DIR
    logs_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    run_log = logs_dir / f"trading_bot_{timestamp}.log"

    file_h = logging.FileHandler(run_log, encoding="utf-8")
    file_h.setLevel(logging.DEBUG)
    file_h.setFormatter(fmt)
    log.addHandler(file_h)

    # ── Stable symlink: trading_bot.log → current run ──
    stable = logs_dir / "trading_bot.log"
    try:
        if stable.exists() or stable.is_symlink():
            stable.unlink()
        # Copy as a fresh file (symlinks can be tricky on Windows)
        # The file handler writes to run_log; we'll copy at the end,
        # but for live tailing we just point the stable name at the same file.
        # Simplest Windows-safe approach: make stable a second handler.
        stable_h = logging.FileHandler(stable, mode="w", encoding="utf-8")
        stable_h.setLevel(logging.DEBUG)
        stable_h.setFormatter(fmt)
        log.addHandler(stable_h)
    except OSError:
        pass  # Non-critical if stable link fails

    # ── Prune old logs ──
    deleted = prune_old_files(logs_dir, "trading_bot_*.log")
    if deleted:
        log.debug("Pruned %d old log file(s)", deleted)

    # ── Prune old reports (health + audit) ──
    reports_dir = settings.BASE_DIR / "reports"
    if reports_dir.exists():
        d1 = prune_old_files(reports_dir, "health_*.md")
        d2 = prune_old_files(reports_dir, "strategist_audit_*.md")
        if d1 or d2:
            log.debug(
                "Pruned %d old health report(s) and %d old audit report(s)",
                d1, d2,
            )

    log.info("Log started: %s", run_log.name)
    return log


logger = _setup_logger()
