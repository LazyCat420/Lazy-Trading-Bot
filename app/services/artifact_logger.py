"""Artifact Logger — dumps raw pipeline context to disk per trading cycle.

Each cycle gets a timestamped directory under `data/artifacts/` containing:
  - context.json     : raw market context passed to the LLM
  - prompt.txt       : the full LLM system + user prompt
  - response.txt     : raw LLM response text
  - decision.json    : parsed TradeAction
  - execution.json   : execution result (if any)

These files enable full post-mortem debugging without DB queries.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.utils.logger import logger

# ── Default output directory ──────────────────────────────────────
_ARTIFACTS_DIR = Path("data/artifacts")


class ArtifactLogger:
    """Persist raw pipeline inputs/outputs to disk per cycle."""

    def __init__(self, base_dir: Path | str | None = None) -> None:
        self._base = Path(base_dir) if base_dir else _ARTIFACTS_DIR

    def start_cycle(self, cycle_id: str) -> Path:
        """Create and return the directory for a new trading cycle."""
        ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        cycle_dir = self._base / f"{ts}_{cycle_id[:8]}"
        cycle_dir.mkdir(parents=True, exist_ok=True)
        return cycle_dir

    @staticmethod
    def save_context(cycle_dir: Path, ticker: str, context: dict) -> None:
        """Save raw market context for a ticker."""
        try:
            out = cycle_dir / f"{ticker}_context.json"
            out.write_text(
                json.dumps(context, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("[ArtifactLogger] Failed to save context: %s", exc)

    @staticmethod
    def save_prompt(cycle_dir: Path, ticker: str, system: str, user: str) -> None:
        """Save the full LLM prompt (system + user)."""
        try:
            out = cycle_dir / f"{ticker}_prompt.txt"
            out.write_text(
                f"=== SYSTEM PROMPT ===\n{system}\n\n"
                f"=== USER PROMPT ===\n{user}\n",
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("[ArtifactLogger] Failed to save prompt: %s", exc)

    @staticmethod
    def save_response(cycle_dir: Path, ticker: str, raw_text: str) -> None:
        """Save the raw LLM response."""
        try:
            out = cycle_dir / f"{ticker}_response.txt"
            out.write_text(raw_text, encoding="utf-8")
        except Exception as exc:
            logger.warning("[ArtifactLogger] Failed to save response: %s", exc)

    @staticmethod
    def save_decision(cycle_dir: Path, ticker: str, decision: dict) -> None:
        """Save the parsed TradeAction as JSON."""
        try:
            out = cycle_dir / f"{ticker}_decision.json"
            out.write_text(
                json.dumps(decision, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("[ArtifactLogger] Failed to save decision: %s", exc)

    @staticmethod
    def save_execution(cycle_dir: Path, ticker: str, result: dict) -> None:
        """Save the execution result."""
        try:
            out = cycle_dir / f"{ticker}_execution.json"
            out.write_text(
                json.dumps(result, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("[ArtifactLogger] Failed to save execution: %s", exc)

    @staticmethod
    def save_summary(cycle_dir: Path, summary: dict) -> None:
        """Save a cycle-level summary (all tickers, timing, stats)."""
        try:
            out = cycle_dir / "cycle_summary.json"
            out.write_text(
                json.dumps(summary, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("[ArtifactLogger] Failed to save summary: %s", exc)
