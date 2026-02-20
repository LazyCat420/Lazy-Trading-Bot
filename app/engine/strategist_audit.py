"""Strategist Audit Logger — captures every LLM decision turn for diagnosis.

Creates a per-run audit trail that records:
  • Every LLM turn: raw prompt → raw response → parsed action → tool result
  • Data completeness per ticker (flags missing fields)
  • Summary of why trades were/weren't made
  • Writes Markdown reports to reports/strategist_audit_*.md
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.utils.logger import logger

REPORTS_DIR = Path(__file__).resolve().parents[2] / "reports"

# Fields expected in every dossier — missing ones are flagged
_REQUIRED_DOSSIER_FIELDS = [
    "executive_summary",
    "bull_case",
    "bear_case",
    "key_catalysts",
    "conviction_score",
    "sector",
    "industry",
    "market_cap_tier",
]

_REQUIRED_SCORECARD_FIELDS = [
    "trend_template_score",
    "vcp_setup_score",
    "relative_strength_rating",
]


class StrategistAudit:
    """Accumulates audit data during a Portfolio Strategist run."""

    def __init__(self) -> None:
        self._started_at = datetime.now()
        self._turns: list[dict[str, Any]] = []
        self._candidate_gaps: dict[str, list[str]] = {}
        self._summary: str = ""
        self._orders: list[dict] = []
        self._finish_reason: str = ""

    # ------------------------------------------------------------------
    # Recording helpers
    # ------------------------------------------------------------------

    def log_turn(
        self,
        turn_number: int,
        raw_llm_output: str,
        parsed_action: str,
        parsed_params: dict,
        tool_result: dict | None = None,
    ) -> None:
        """Record one action-loop turn."""
        entry = {
            "turn": turn_number,
            "timestamp": datetime.now().isoformat(),
            "raw_llm_output": raw_llm_output[:2000],
            "parsed_action": parsed_action,
            "parsed_params": parsed_params,
            "tool_result": tool_result,
        }
        self._turns.append(entry)
        logger.info(
            "[Audit] Turn %d: action=%s params=%s",
            turn_number, parsed_action, json.dumps(parsed_params)[:200],
        )

    def log_bad_json(self, turn_number: int, raw_output: str) -> None:
        """Record a turn where the LLM produced invalid JSON."""
        self._turns.append({
            "turn": turn_number,
            "timestamp": datetime.now().isoformat(),
            "raw_llm_output": raw_output[:2000],
            "parsed_action": "INVALID_JSON",
            "parsed_params": {},
            "tool_result": None,
            "error": "LLM produced invalid JSON",
        })
        logger.warning("[Audit] Turn %d: INVALID JSON from LLM", turn_number)

    def log_candidates(self, candidates: list[dict]) -> None:
        """Scan candidate dossier data for missing/empty fields."""
        for c in candidates:
            ticker = c.get("ticker", "UNKNOWN")
            gaps: list[str] = []

            # Check top-level dossier fields
            for field in _REQUIRED_DOSSIER_FIELDS:
                val = c.get(field)
                if val is None or val == "" or val == 0 or val == []:
                    gaps.append(f"missing `{field}`")

            # Check scorecard fields
            scorecard = c.get("scorecard", {})
            for field in _REQUIRED_SCORECARD_FIELDS:
                val = scorecard.get(field) if scorecard else None
                top_val = c.get(field)  # might be at top level
                if (val is None or val == 0) and (top_val is None or top_val == 0):
                    gaps.append(f"missing scorecard.`{field}`")

            # Check conviction score range
            conv = c.get("conviction_score", 0.5)
            if 0.45 <= conv <= 0.55:
                gaps.append(
                    f"conviction={conv:.2f} is in the 'dead zone' (0.45-0.55)"
                )

            if gaps:
                self._candidate_gaps[ticker] = gaps
                logger.info(
                    "[Audit] %s has %d data gaps: %s",
                    ticker, len(gaps), ", ".join(gaps[:3]),
                )

    def log_finish(self, reason: str, orders: list[dict]) -> None:
        """Record the final outcome."""
        self._finish_reason = reason
        self._orders = orders

    # ------------------------------------------------------------------
    # Report generation
    # ------------------------------------------------------------------

    def generate_report(self) -> str:
        """Generate a Markdown audit report and write it to disk.

        Returns the absolute path to the report file.
        """
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        ts = self._started_at.strftime("%Y-%m-%d_%H%M%S")
        path = REPORTS_DIR / f"strategist_audit_{ts}.md"

        lines: list[str] = []
        lines.append("# Portfolio Strategist Audit Report\n")
        lines.append(f"**Run started:** {self._started_at.isoformat()}")
        lines.append(f"**Turns used:** {len(self._turns)}")
        lines.append(f"**Orders placed:** {len(self._orders)}")
        lines.append(f"**Finish reason:** {self._finish_reason}\n")

        # ── Data Completeness ────────────────────────────────────
        lines.append("## Data Completeness\n")
        if self._candidate_gaps:
            lines.append(
                f"⚠️ **{len(self._candidate_gaps)} tickers have data gaps:**\n"
            )
            lines.append("| Ticker | Gaps |")
            lines.append("|--------|------|")
            for ticker, gaps in sorted(self._candidate_gaps.items()):
                lines.append(f"| {ticker} | {', '.join(gaps)} |")
            lines.append("")
        else:
            lines.append("✅ All candidate tickers have complete data.\n")

        # ── Turn-by-Turn Log ─────────────────────────────────────
        lines.append("## Turn-by-Turn Log\n")
        for t in self._turns:
            turn_n = t["turn"]
            action = t["parsed_action"]
            params = t.get("parsed_params", {})
            result = t.get("tool_result")
            error = t.get("error")

            lines.append(f"### Turn {turn_n}: `{action}`\n")

            if error:
                lines.append(f"❌ **Error:** {error}\n")

            # Params
            if params:
                lines.append("**Params:**")
                lines.append(f"```json\n{json.dumps(params, indent=2)}\n```\n")

            # Tool result (summarized)
            if result:
                result_str = json.dumps(result, indent=2)
                if len(result_str) > 1000:
                    result_str = result_str[:1000] + "\n... (truncated)"
                lines.append("**Result:**")
                lines.append(f"```json\n{result_str}\n```\n")

            # Raw LLM output
            raw = t.get("raw_llm_output", "")
            if raw and action == "INVALID_JSON":
                lines.append("**Raw LLM output:**")
                lines.append(f"```\n{raw[:500]}\n```\n")

        # ── Orders Summary ───────────────────────────────────────
        if self._orders:
            lines.append("## Orders Placed\n")
            lines.append("| Side | Ticker | Qty | Price | Reason |")
            lines.append("|------|--------|-----|-------|--------|")
            for o in self._orders:
                lines.append(
                    f"| {o.get('side', '?')} | {o.get('ticker', '?')} "
                    f"| {o.get('qty', 0)} | ${o.get('price', 0):.2f} "
                    f"| {o.get('reason', '')[:80]} |"
                )
            lines.append("")
        else:
            lines.append("## Orders Placed\n")
            lines.append("🔴 **No orders were placed this run.**\n")
            lines.append(
                "Check the turn log above to understand why the LLM "
                "chose not to trade.\n"
            )

        report = "\n".join(lines)
        path.write_text(report, encoding="utf-8")
        logger.info("[Audit] Report written to %s", path)
        return str(path)
