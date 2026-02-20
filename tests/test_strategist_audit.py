"""Tests for the StrategistAudit logger."""

from __future__ import annotations

import json
from pathlib import Path

from app.engine.strategist_audit import StrategistAudit


class TestStrategistAudit:
    """Tests for the audit logging system."""

    def test_log_turn(self) -> None:
        """Test that turns are recorded correctly."""
        audit = StrategistAudit()
        audit.log_turn(
            turn_number=1,
            raw_llm_output='{"action": "get_portfolio", "params": {}}',
            parsed_action="get_portfolio",
            parsed_params={},
            tool_result={"cash_balance": 10000},
        )
        assert len(audit._turns) == 1
        assert audit._turns[0]["parsed_action"] == "get_portfolio"

    def test_log_bad_json(self) -> None:
        """Test that invalid JSON turns are flagged."""
        audit = StrategistAudit()
        audit.log_bad_json(1, "not valid json {{{")
        assert len(audit._turns) == 1
        assert audit._turns[0]["parsed_action"] == "INVALID_JSON"
        assert "error" in audit._turns[0]

    def test_log_candidates_detects_gaps(self) -> None:
        """Test that missing dossier fields are flagged."""
        audit = StrategistAudit()
        candidates = [
            {
                "ticker": "AAPL",
                "executive_summary": "Apple is strong",
                "bull_case": "",  # gap
                "bear_case": "Some risk",
                "key_catalysts": [],  # gap
                "conviction_score": 0.50,  # dead zone
                "sector": "Technology",
                "industry": "Consumer Electronics",
                "market_cap_tier": "mega",
                "scorecard": {
                    "trend_template_score": 85,
                    "vcp_setup_score": 0,  # gap
                    "relative_strength_rating": 70,
                },
            },
        ]
        audit.log_candidates(candidates)
        assert "AAPL" in audit._candidate_gaps
        gaps = audit._candidate_gaps["AAPL"]
        assert any("bull_case" in g for g in gaps)
        assert any("catalysts" in g for g in gaps)
        assert any("dead zone" in g for g in gaps)

    def test_generate_report(self, tmp_path: Path, monkeypatch) -> None:
        """Test that a Markdown report is generated."""
        # Redirect reports to tmp dir
        monkeypatch.setattr(
            "app.engine.strategist_audit.REPORTS_DIR", tmp_path,
        )

        audit = StrategistAudit()
        audit.log_turn(1, '{"action":"get_portfolio","params":{}}',
                       "get_portfolio", {}, {"cash": 10000})
        audit.log_turn(2, '{"action":"finish","params":{"summary":"test"}}',
                       "finish", {"summary": "test"}, None)
        audit.log_finish("test finish", [])

        path_str = audit.generate_report()
        path = Path(path_str)
        assert path.exists()

        content = path.read_text(encoding="utf-8")
        assert "Audit Report" in content
        assert "Turn 1" in content
        assert "Turn 2" in content
        assert "No orders were placed" in content

    def test_generate_report_with_orders(self, tmp_path: Path, monkeypatch) -> None:
        """Test report reflects orders placed."""
        monkeypatch.setattr(
            "app.engine.strategist_audit.REPORTS_DIR", tmp_path,
        )

        audit = StrategistAudit()
        orders = [
            {"side": "buy", "ticker": "NVDA", "qty": 10,
             "price": 150.0, "reason": "Strong momentum"},
        ]
        audit.log_finish("Bought NVDA", orders)
        path_str = audit.generate_report()
        content = Path(path_str).read_text(encoding="utf-8")
        assert "NVDA" in content
        assert "Strong momentum" in content
