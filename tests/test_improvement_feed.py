"""Tests for ImprovementFeed — self-improving diagnostics aggregator.

Uses an in-memory DuckDB instance so tests are fast and isolated.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import duckdb
import pytest


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture()
def _in_memory_db(tmp_path):
  """Create an in-memory DuckDB with the schema needed by ImprovementFeed.

  Patches app.database.get_db to return this connection.
  """
  conn = duckdb.connect(":memory:")

  # Create required tables
  conn.execute("""
    CREATE TABLE llm_audit_logs (
      id               VARCHAR PRIMARY KEY,
      cycle_id         VARCHAR DEFAULT '',
      ticker           VARCHAR DEFAULT '',
      agent_step       VARCHAR DEFAULT '',
      system_prompt    TEXT DEFAULT '',
      user_context     TEXT DEFAULT '',
      raw_response     TEXT DEFAULT '',
      parsed_json      TEXT,
      tokens_used      INTEGER DEFAULT 0,
      execution_time_ms INTEGER DEFAULT 0,
      model            VARCHAR DEFAULT '',
      created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
  """)

  conn.execute("""
    CREATE TABLE trade_decisions (
      id               VARCHAR PRIMARY KEY,
      bot_id           VARCHAR NOT NULL,
      symbol           VARCHAR NOT NULL,
      ts               TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      action           VARCHAR NOT NULL,
      confidence       DOUBLE,
      rationale        TEXT,
      risk_level       VARCHAR DEFAULT 'MED',
      risk_notes       TEXT DEFAULT '',
      time_horizon     VARCHAR DEFAULT 'SWING',
      raw_llm_response TEXT,
      status           VARCHAR DEFAULT 'pending',
      rejection_reason TEXT DEFAULT ''
    )
  """)

  conn.execute("""
    CREATE TABLE trade_executions (
      id               VARCHAR PRIMARY KEY,
      decision_id      VARCHAR NOT NULL,
      order_id         VARCHAR DEFAULT '',
      ts               TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      filled_qty       INTEGER DEFAULT 0,
      avg_price        DOUBLE DEFAULT 0,
      status           VARCHAR DEFAULT 'pending',
      broker_error     TEXT DEFAULT ''
    )
  """)

  conn.execute("""
    CREATE SEQUENCE IF NOT EXISTS audit_report_seq START 1
  """)
  conn.execute("""
    CREATE TABLE bot_audit_reports (
      id               INTEGER PRIMARY KEY DEFAULT nextval('audit_report_seq'),
      audited_bot_id   VARCHAR NOT NULL,
      auditor_bot_id   VARCHAR NOT NULL,
      overall_score    FLOAT DEFAULT 0.0,
      categories       TEXT DEFAULT '{}',
      recommendations  TEXT DEFAULT '[]',
      critical_issues  TEXT DEFAULT '[]',
      created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
  """)

  conn.execute("""
    CREATE TABLE portfolio_snapshots (
      timestamp              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      cash_balance           DOUBLE NOT NULL,
      total_positions_value  DOUBLE DEFAULT 0,
      total_portfolio_value  DOUBLE DEFAULT 0,
      realized_pnl           DOUBLE DEFAULT 0,
      unrealized_pnl         DOUBLE DEFAULT 0,
      bot_id                 VARCHAR NOT NULL DEFAULT 'default'
    )
  """)

  conn.execute("""
    CREATE SEQUENCE IF NOT EXISTS pipeline_events_seq START 1
  """)
  conn.execute("""
    CREATE TABLE pipeline_events (
      id               INTEGER PRIMARY KEY DEFAULT nextval('pipeline_events_seq'),
      bot_id           VARCHAR NOT NULL,
      event_type       VARCHAR NOT NULL,
      phase            VARCHAR DEFAULT '',
      ticker           VARCHAR DEFAULT '',
      detail           VARCHAR DEFAULT '',
      status           VARCHAR DEFAULT 'success',
      event_data       TEXT,
      created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
  """)

  conn.execute("""
    CREATE TABLE benchmark_stats (
      id                       VARCHAR PRIMARY KEY,
      cycle_id                 VARCHAR DEFAULT '',
      bot_id                   VARCHAR DEFAULT 'default',
      timestamp                TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      json_parse_success_rate  DOUBLE DEFAULT 0.0,
      trade_accuracy           DOUBLE DEFAULT 0.0,
      avg_llm_latency_ms       INTEGER DEFAULT 0,
      data_completeness        DOUBLE DEFAULT 0.0,
      cross_audit_score        DOUBLE DEFAULT 0.0,
      total_errors             INTEGER DEFAULT 0,
      total_warnings           INTEGER DEFAULT 0,
      total_llm_calls          INTEGER DEFAULT 0,
      total_tokens_used        INTEGER DEFAULT 0,
      decisions_made           INTEGER DEFAULT 0,
      trades_executed          INTEGER DEFAULT 0,
      trades_rejected          INTEGER DEFAULT 0,
      portfolio_pnl            DOUBLE DEFAULT 0.0
    )
  """)

  with patch("app.services.ImprovementFeed.get_db", return_value=conn):
    yield conn

  conn.close()


@pytest.fixture()
def _reports_dir(tmp_path):
  """Override REPORTS_DIR to a temp directory."""
  reports = tmp_path / "reports"
  reports.mkdir()
  with patch("app.services.ImprovementFeed.REPORTS_DIR", reports):
    yield reports


# ── Test: Empty Database ──────────────────────────────────────────────


class TestEmptyDatabase:
  """Tests that the feed handles an empty database gracefully."""

  def test_generate_report_empty_db(self, _in_memory_db, _reports_dir):
    from app.services.ImprovementFeed import ImprovementFeed

    feed = ImprovementFeed(lookback_hours=24)
    path = feed.generate_report()

    assert path is not None
    content = Path(path).read_text(encoding="utf-8")
    assert "# Improvement Feed" in content
    assert "No issues detected" in content

  def test_priority_queue_empty(self, _in_memory_db, _reports_dir):
    from app.services.ImprovementFeed import ImprovementFeed

    feed = ImprovementFeed(lookback_hours=24)
    errors = feed._query_pipeline_errors()
    quality = feed._query_llm_quality()
    audits = feed._query_cross_audits()
    trades = feed._query_trade_accuracy()
    gaps = feed._query_data_gaps()

    priority = feed._build_priority_queue(errors, quality, audits, trades, gaps)
    assert priority == []


# ── Test: Pipeline Errors Detection ───────────────────────────────────


class TestPipelineErrors:
  """Tests error and failure detection from llm_audit_logs and pipeline_events."""

  def test_detects_json_parse_failures(self, _in_memory_db, _reports_dir):
    conn = _in_memory_db
    # Insert a non-JSON response (should be detected as parse failure)
    conn.execute(
      "INSERT INTO llm_audit_logs (id, agent_step, model, raw_response, "
      "execution_time_ms, tokens_used, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
      ["log1", "trading", "llama3", "Not JSON at all", 5000, 100, datetime.now()],
    )
    # Insert a valid JSON response
    conn.execute(
      "INSERT INTO llm_audit_logs (id, agent_step, model, raw_response, "
      "execution_time_ms, tokens_used, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
      ["log2", "analysis", "llama3", '{"action": "BUY"}', 3000, 80, datetime.now()],
    )

    from app.services.ImprovementFeed import ImprovementFeed

    feed = ImprovementFeed(lookback_hours=24)
    errors = feed._query_pipeline_errors()

    assert errors["llm_total_calls"] == 2
    assert errors["llm_json_parse_failures"] == 1
    assert len(errors["llm_failures"]) == 1
    assert errors["llm_failures"][0]["step"] == "trading"

  def test_detects_timeouts(self, _in_memory_db, _reports_dir):
    conn = _in_memory_db
    # Insert a timed-out call (>120s)
    conn.execute(
      "INSERT INTO llm_audit_logs (id, agent_step, model, raw_response, "
      "execution_time_ms, tokens_used, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
      ["log3", "trading", "llama3", '{"ok": true}', 150_000, 100, datetime.now()],
    )

    from app.services.ImprovementFeed import ImprovementFeed

    feed = ImprovementFeed(lookback_hours=24)
    errors = feed._query_pipeline_errors()

    assert errors["llm_timeouts"] == 1

  def test_detects_pipeline_errors(self, _in_memory_db, _reports_dir):
    conn = _in_memory_db
    conn.execute(
      "INSERT INTO pipeline_events (bot_id, event_type, phase, detail, "
      "status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
      ["bot1", "collection_fail", "collection", "yfinance timeout", "error", datetime.now()],
    )

    from app.services.ImprovementFeed import ImprovementFeed

    feed = ImprovementFeed(lookback_hours=24)
    errors = feed._query_pipeline_errors()

    assert len(errors["pipeline_errors"]) == 1
    assert errors["pipeline_errors"][0]["phase"] == "collection"


# ── Test: LLM Quality Scorecard ───────────────────────────────────────


class TestLLMQuality:
  """Tests LLM quality aggregation by agent_step."""

  def test_aggregates_by_step(self, _in_memory_db, _reports_dir):
    conn = _in_memory_db
    now = datetime.now()
    for i in range(5):
      conn.execute(
        "INSERT INTO llm_audit_logs (id, agent_step, model, raw_response, "
        "execution_time_ms, tokens_used, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [f"q{i}", "analysis", "llama3", '{"ok": true}', 3000 + i * 1000, 100, now],
      )
    for i in range(3):
      conn.execute(
        "INSERT INTO llm_audit_logs (id, agent_step, model, raw_response, "
        "execution_time_ms, tokens_used, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [f"t{i}", "trading", "llama3", '{"ok": true}', 2000, 50, now],
      )

    from app.services.ImprovementFeed import ImprovementFeed

    feed = ImprovementFeed(lookback_hours=24)
    quality = feed._query_llm_quality()

    assert "analysis" in quality["steps"]
    assert quality["steps"]["analysis"]["total_calls"] == 5
    assert "trading" in quality["steps"]
    assert quality["steps"]["trading"]["total_calls"] == 3
    assert quality["overall"]["total_calls"] == 8


# ── Test: Trade Decision Accuracy ─────────────────────────────────────


class TestTradeAccuracy:
  """Tests trade decision analysis."""

  def test_counts_by_action_and_status(self, _in_memory_db, _reports_dir):
    conn = _in_memory_db
    now = datetime.now()
    decisions = [
      ("d1", "bot1", "AAPL", now, "BUY", 0.85, "Strong momentum", "executed"),
      ("d2", "bot1", "TSLA", now, "SELL", 0.70, "Weak technicals", "executed"),
      ("d3", "bot1", "MSFT", now, "HOLD", 0.50, "Neutral", "pending"),
      ("d4", "bot1", "GOOG", now, "BUY", 0.90, "Great fundamentals", "rejected"),
    ]
    for d in decisions:
      conn.execute(
        "INSERT INTO trade_decisions (id, bot_id, symbol, ts, action, "
        "confidence, rationale, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        list(d),
      )

    from app.services.ImprovementFeed import ImprovementFeed

    feed = ImprovementFeed(lookback_hours=24)
    trades = feed._query_trade_accuracy()

    assert trades["total_decisions"] == 4
    assert trades["by_action"]["BUY"]["count"] == 2
    assert trades["by_action"]["SELL"]["count"] == 1
    assert trades["by_action"]["HOLD"]["count"] == 1
    assert trades["by_status"]["executed"] == 2
    assert trades["by_status"]["rejected"] == 1

  def test_confidence_calibration(self, _in_memory_db, _reports_dir):
    conn = _in_memory_db
    now = datetime.now()
    conn.execute(
      "INSERT INTO trade_decisions (id, bot_id, symbol, ts, action, confidence, status) "
      "VALUES (?, ?, ?, ?, ?, ?, ?)",
      ["cal1", "bot1", "AAPL", now, "BUY", 0.90, "executed"],
    )
    conn.execute(
      "INSERT INTO trade_decisions (id, bot_id, symbol, ts, action, confidence, status) "
      "VALUES (?, ?, ?, ?, ?, ?, ?)",
      ["cal2", "bot1", "TSLA", now, "BUY", 0.40, "rejected"],
    )

    from app.services.ImprovementFeed import ImprovementFeed

    feed = ImprovementFeed(lookback_hours=24)
    trades = feed._query_trade_accuracy()

    assert len(trades["confidence_calibration"]) >= 1


# ── Test: Cross-Audit Integration ─────────────────────────────────────


class TestCrossAudit:
  """Tests cross-bot audit score aggregation."""

  def test_aggregates_audit_scores(self, _in_memory_db, _reports_dir):
    conn = _in_memory_db
    now = datetime.now()
    categories = json.dumps({
      "data_quality": {"score": 7},
      "risk_management": {"score": 5},
    })
    recs = json.dumps(["Improve data pipeline", "Add more risk checks"])
    crits = json.dumps(["Missing stop-loss on 2 positions"])

    conn.execute(
      "INSERT INTO bot_audit_reports (audited_bot_id, auditor_bot_id, "
      "overall_score, categories, recommendations, critical_issues, created_at) "
      "VALUES (?, ?, ?, ?, ?, ?, ?)",
      ["bot1", "bot2", 6.5, categories, recs, crits, now],
    )

    from app.services.ImprovementFeed import ImprovementFeed

    feed = ImprovementFeed(lookback_hours=24)
    audits = feed._query_cross_audits()

    assert audits["avg_score"] == 6.5
    assert len(audits["audits"]) == 1
    assert "Improve data pipeline" in audits["top_recommendations"]
    assert "Missing stop-loss on 2 positions" in audits["critical_issues"]


# ── Test: Priority Queue Logic ────────────────────────────────────────


class TestPriorityQueue:
  """Tests the priority queue synthesis."""

  def test_critical_json_failure_rate(self, _in_memory_db, _reports_dir):
    from app.services.ImprovementFeed import ImprovementFeed

    feed = ImprovementFeed(lookback_hours=24)
    errors = {
      "llm_total_calls": 10,
      "llm_json_parse_failures": 5,  # 50% failure rate
      "llm_timeouts": 0,
      "pipeline_errors": [],
    }
    priority = feed._build_priority_queue(
      errors, {"steps": {}}, {"avg_score": 0}, {"by_action": {}, "by_status": {}}, {},
    )

    assert len(priority) >= 1
    assert priority[0]["severity"] == "CRITICAL"
    assert "JSON parse failure" in priority[0]["issue"]

  def test_high_timeout_detection(self, _in_memory_db, _reports_dir):
    from app.services.ImprovementFeed import ImprovementFeed

    feed = ImprovementFeed(lookback_hours=24)
    errors = {
      "llm_total_calls": 10,
      "llm_json_parse_failures": 0,
      "llm_timeouts": 3,
      "pipeline_errors": [],
    }
    priority = feed._build_priority_queue(
      errors, {"steps": {}}, {"avg_score": 0}, {"by_action": {}, "by_status": {}}, {},
    )

    has_timeout = any(
      "timed out" in p["issue"] for p in priority
    )
    assert has_timeout

  def test_medium_hold_indecisiveness(self, _in_memory_db, _reports_dir):
    from app.services.ImprovementFeed import ImprovementFeed

    feed = ImprovementFeed(lookback_hours=24)
    trades = {
      "by_action": {
        "HOLD": {"count": 8},
        "BUY": {"count": 1},
        "SELL": {"count": 1},
      },
      "by_status": {},
    }
    priority = feed._build_priority_queue(
      {"llm_total_calls": 0, "llm_json_parse_failures": 0, "llm_timeouts": 0, "pipeline_errors": []},
      {"steps": {}},
      {"avg_score": 0},
      trades,
      {},
    )

    has_indecisive = any(
      "indecisive" in p["issue"] for p in priority
    )
    assert has_indecisive

  def test_severity_ordering(self, _in_memory_db, _reports_dir):
    from app.services.ImprovementFeed import ImprovementFeed

    feed = ImprovementFeed(lookback_hours=24)
    errors = {
      "llm_total_calls": 10,
      "llm_json_parse_failures": 5,
      "llm_timeouts": 2,
      "pipeline_errors": [{"detail": "crash"}],
    }
    priority = feed._build_priority_queue(
      errors, {"steps": {}}, {"avg_score": 0}, {"by_action": {}, "by_status": {}}, {},
    )

    severities = [p["severity"] for p in priority]
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    for i in range(len(severities) - 1):
      assert order[severities[i]] <= order[severities[i + 1]]


# ── Test: Report Generation ───────────────────────────────────────────


class TestReportGeneration:
  """Tests report file generation."""

  def test_report_file_created(self, _in_memory_db, _reports_dir):
    from app.services.ImprovementFeed import ImprovementFeed

    feed = ImprovementFeed(lookback_hours=24)
    path = feed.generate_report()

    assert Path(path).exists()
    content = Path(path).read_text(encoding="utf-8")
    assert "# Improvement Feed" in content
    assert "## Priority Queue" in content
    assert "## Section 1" in content
    assert "## Section 2" in content
    assert "## Section 3" in content
    assert "## Section 4" in content
    assert "## Section 5" in content
    assert "## Section 6" in content

  def test_report_with_data(self, _in_memory_db, _reports_dir):
    conn = _in_memory_db
    now = datetime.now()

    # Add some LLM logs
    conn.execute(
      "INSERT INTO llm_audit_logs (id, agent_step, model, raw_response, "
      "execution_time_ms, tokens_used, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
      ["rpt1", "trading", "llama3", "INVALID OUTPUT", 5000, 100, now],
    )
    conn.execute(
      "INSERT INTO llm_audit_logs (id, agent_step, model, raw_response, "
      "execution_time_ms, tokens_used, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
      ["rpt2", "analysis", "llama3", '{"ok": true}', 3000, 80, now],
    )

    # Add a trade decision
    conn.execute(
      "INSERT INTO trade_decisions (id, bot_id, symbol, ts, action, confidence, status) "
      "VALUES (?, ?, ?, ?, ?, ?, ?)",
      ["rpt_d1", "bot1", "AAPL", now, "BUY", 0.85, "executed"],
    )

    from app.services.ImprovementFeed import ImprovementFeed

    feed = ImprovementFeed(lookback_hours=24)
    path = feed.generate_report()

    content = Path(path).read_text(encoding="utf-8")
    assert "Total LLM calls" in content
    assert "JSON parse failures" in content
    assert "Trade Decision Accuracy" in content

  def test_get_latest_report(self, _in_memory_db, _reports_dir):
    from app.services.ImprovementFeed import ImprovementFeed

    feed = ImprovementFeed(lookback_hours=24)
    feed.generate_report()

    latest_path = feed.get_latest_report_path()
    assert latest_path is not None
    assert "improvement_feed_" in latest_path

    content = feed.get_latest_report_content()
    assert "# Improvement Feed" in content


# ── Test: Benchmark Stats ─────────────────────────────────────────────


class TestBenchmarkStats:
  """Tests benchmark stats recording."""

  def test_records_stats_to_db(self, _in_memory_db, _reports_dir):
    conn = _in_memory_db
    now = datetime.now()

    # Add some data
    conn.execute(
      "INSERT INTO llm_audit_logs (id, agent_step, model, raw_response, "
      "execution_time_ms, tokens_used, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
      ["bs1", "trading", "llama3", '{"ok": true}', 5000, 100, now],
    )
    conn.execute(
      "INSERT INTO trade_decisions (id, bot_id, symbol, ts, action, confidence, status) "
      "VALUES (?, ?, ?, ?, ?, ?, ?)",
      ["bs_d1", "bot1", "AAPL", now, "BUY", 0.85, "executed"],
    )

    from app.services.ImprovementFeed import ImprovementFeed

    feed = ImprovementFeed(lookback_hours=24)
    stats = feed.record_benchmark_stats(cycle_id="test-cycle", bot_id="bot1")

    assert stats["total_llm_calls"] == 1
    assert stats["decisions_made"] == 1
    assert stats["trades_executed"] == 1
    assert stats["json_parse_success_rate"] == 1.0

    # Verify it was persisted
    rows = conn.execute("SELECT * FROM benchmark_stats").fetchall()
    assert len(rows) == 1

  def test_stats_with_failures(self, _in_memory_db, _reports_dir):
    conn = _in_memory_db
    now = datetime.now()

    # 2 calls: 1 valid JSON, 1 invalid
    conn.execute(
      "INSERT INTO llm_audit_logs (id, agent_step, model, raw_response, "
      "execution_time_ms, tokens_used, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
      ["sf1", "trading", "llama3", '{"ok": true}', 5000, 100, now],
    )
    conn.execute(
      "INSERT INTO llm_audit_logs (id, agent_step, model, raw_response, "
      "execution_time_ms, tokens_used, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
      ["sf2", "trading", "llama3", "BROKEN OUTPUT", 3000, 80, now],
    )

    from app.services.ImprovementFeed import ImprovementFeed

    feed = ImprovementFeed(lookback_hours=24)
    stats = feed.record_benchmark_stats(cycle_id="fail-cycle")

    assert stats["json_parse_success_rate"] == 0.5
    assert stats["total_llm_calls"] == 2


# ── Test: Lookback Filtering ──────────────────────────────────────────


class TestLookbackFiltering:
  """Tests that old data outside the lookback window is excluded."""

  def test_excludes_old_data(self, _in_memory_db, _reports_dir):
    conn = _in_memory_db
    old_time = datetime.now() - timedelta(hours=48)
    new_time = datetime.now()

    # Old log (outside 24h window)
    conn.execute(
      "INSERT INTO llm_audit_logs (id, agent_step, model, raw_response, "
      "execution_time_ms, tokens_used, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
      ["old1", "trading", "llama3", '{"ok": true}', 5000, 100, old_time],
    )
    # New log (inside 24h window)
    conn.execute(
      "INSERT INTO llm_audit_logs (id, agent_step, model, raw_response, "
      "execution_time_ms, tokens_used, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
      ["new1", "trading", "llama3", '{"ok": true}', 3000, 80, new_time],
    )

    from app.services.ImprovementFeed import ImprovementFeed

    feed = ImprovementFeed(lookback_hours=24)
    errors = feed._query_pipeline_errors()

    assert errors["llm_total_calls"] == 1  # Only the new one


# ── Test: Portfolio Stats ─────────────────────────────────────────────


class TestPortfolioStats:
  """Tests portfolio snapshot aggregation."""

  def test_calculates_pnl(self, _in_memory_db, _reports_dir):
    conn = _in_memory_db
    now = datetime.now()

    conn.execute(
      "INSERT INTO portfolio_snapshots (timestamp, cash_balance, "
      "total_portfolio_value, bot_id) VALUES (?, ?, ?, ?)",
      [now - timedelta(hours=2), 10000, 50000, "bot1"],
    )
    conn.execute(
      "INSERT INTO portfolio_snapshots (timestamp, cash_balance, "
      "total_portfolio_value, bot_id) VALUES (?, ?, ?, ?)",
      [now, 10000, 52000, "bot1"],
    )

    from app.services.ImprovementFeed import ImprovementFeed

    feed = ImprovementFeed(lookback_hours=24)
    portfolio = feed._query_portfolio_stats()

    assert len(portfolio["bots"]) == 1
    assert portfolio["bots"][0]["pnl"] == 2000.0
    assert portfolio["total_pnl"] == 2000.0
