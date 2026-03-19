"""ImprovementFeed -- self-improving diagnostics aggregator.

Queries all 6 data sources (LLMAuditLogger, DecisionLogger,
CrossBotAuditor, StrategistAudit, HealthTracker, ArtifactLogger)
and synthesizes them into a single structured report: the
"Improvement Feed."

The feed is a Markdown file designed to be consumed by an AI
assistant to identify exactly what needs fixing in the pipeline.
"""

from __future__ import annotations

from app.services.unified_logger import track_class_telemetry, track_telemetry
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.database import get_db
from app.utils.logger import logger

REPORTS_DIR = Path(__file__).resolve().parents[2] / "reports"


@track_class_telemetry
class ImprovementFeed:
  """Aggregates pipeline diagnostics into an AI-readable improvement feed."""

  def __init__(self, lookback_hours: int = 24) -> None:
    self._lookback_hours = lookback_hours
    self._cutoff = datetime.now() - timedelta(hours=lookback_hours)

  # ── Section 1: Pipeline Errors & Failures ──────────────────────

  def _query_pipeline_errors(self) -> dict[str, Any]:
    """Query llm_audit_logs and pipeline_events for failures."""
    conn = get_db()
    result: dict[str, Any] = {
      "llm_failures": [],
      "llm_timeouts": 0,
      "llm_total_calls": 0,
      "llm_json_parse_failures": 0,
      "pipeline_errors": [],
    }
    try:
      # LLM audit logs: failures and stats
      rows = conn.execute(
        """
        SELECT agent_step, model, raw_response, execution_time_ms,
               tokens_used, created_at
        FROM llm_audit_logs
        WHERE created_at >= ?
        ORDER BY created_at DESC
        """,
        [self._cutoff],
      ).fetchall()
      result["llm_total_calls"] = len(rows)

      for row in rows:
        step, model, raw_resp, exec_ms, tokens, ts = row
        # Detect JSON parse failures (response not valid JSON)
        if raw_resp:
          raw_stripped = raw_resp.strip()
          if raw_stripped and not raw_stripped.startswith("{") and not raw_stripped.startswith("["):
            result["llm_json_parse_failures"] += 1
            if len(result["llm_failures"]) < 10:
              result["llm_failures"].append({
                "step": step,
                "model": model,
                "time_ms": exec_ms,
                "preview": raw_stripped[:200],
                "ts": str(ts),
              })
        # Detect timeouts (>120s)
        if exec_ms and exec_ms > 120_000:
          result["llm_timeouts"] += 1

    except Exception as exc:
      logger.warning("[ImprovementFeed] LLM audit query failed: %s", exc)

    try:
      # Pipeline events: errors
      rows = conn.execute(
        """
        SELECT phase, event_type, ticker, detail, status, timestamp
        FROM pipeline_events
        WHERE status = 'error' AND timestamp >= ?
        ORDER BY timestamp DESC
        LIMIT 20
        """,
        [self._cutoff],
      ).fetchall()
      for row in rows:
        result["pipeline_errors"].append({
          "phase": row[0],
          "event_type": row[1],
          "ticker": row[2] or "",
          "detail": (row[3] or "")[:300],
          "ts": str(row[5]),
        })
    except Exception as exc:
      logger.warning("[ImprovementFeed] Pipeline events query failed: %s", exc)

    return result

  # ── Section 2: LLM Quality Scorecard ───────────────────────────

  def _query_llm_quality(self) -> dict[str, Any]:
    """Compute LLM quality metrics per agent_step."""
    conn = get_db()
    result: dict[str, Any] = {"steps": {}, "overall": {}}
    try:
      rows = conn.execute(
        """
        SELECT agent_step,
               COUNT(*) as total,
               AVG(execution_time_ms) as avg_ms,
               MAX(execution_time_ms) as max_ms,
               SUM(tokens_used) as total_tokens,
               AVG(tokens_used) as avg_tokens
        FROM llm_audit_logs
        WHERE created_at >= ?
        GROUP BY agent_step
        ORDER BY total DESC
        """,
        [self._cutoff],
      ).fetchall()

      for row in rows:
        step, total, avg_ms, max_ms, total_tok, avg_tok = row
        if not step:
          step = "unknown"
        result["steps"][step] = {
          "total_calls": total,
          "avg_latency_ms": round(avg_ms or 0),
          "max_latency_ms": round(max_ms or 0),
          "total_tokens": int(total_tok or 0),
          "avg_tokens": round(avg_tok or 0),
        }

      # Overall
      overall_row = conn.execute(
        """
        SELECT COUNT(*),
               AVG(execution_time_ms),
               SUM(tokens_used)
        FROM llm_audit_logs
        WHERE created_at >= ?
        """,
        [self._cutoff],
      ).fetchone()
      if overall_row:
        result["overall"] = {
          "total_calls": overall_row[0] or 0,
          "avg_latency_ms": round(overall_row[1] or 0),
          "total_tokens": int(overall_row[2] or 0),
        }
    except Exception as exc:
      logger.warning("[ImprovementFeed] LLM quality query failed: %s", exc)
    return result

  # ── Section 3: Cross-Model Consistency ─────────────────────────

  def _query_cross_audits(self) -> dict[str, Any]:
    """Get cross-bot audit scores and recommendations."""
    conn = get_db()
    result: dict[str, Any] = {
      "audits": [],
      "avg_score": 0.0,
      "top_recommendations": [],
      "critical_issues": [],
    }
    try:
      rows = conn.execute(
        """
        SELECT audited_bot_id, auditor_bot_id, overall_score,
               categories, recommendations, critical_issues, created_at
        FROM bot_audit_reports
        WHERE created_at >= ?
        ORDER BY created_at DESC
        LIMIT 10
        """,
        [self._cutoff],
      ).fetchall()

      scores = []
      all_recs: list[str] = []
      all_critical: list[str] = []

      for row in rows:
        audit_data = {
          "audited": row[0],
          "auditor": row[1],
          "score": row[2] or 0,
          "categories": {},
          "ts": str(row[6]),
        }
        # Parse categories JSON
        try:
          audit_data["categories"] = json.loads(row[3]) if row[3] else {}
        except (json.JSONDecodeError, TypeError):
          pass
        # Parse recommendations
        try:
          recs = json.loads(row[4]) if row[4] else []
          all_recs.extend(recs)
        except (json.JSONDecodeError, TypeError):
          pass
        # Parse critical issues
        try:
          crits = json.loads(row[5]) if row[5] else []
          all_critical.extend(crits)
        except (json.JSONDecodeError, TypeError):
          pass

        scores.append(row[2] or 0)
        result["audits"].append(audit_data)

      result["avg_score"] = round(sum(scores) / len(scores), 1) if scores else 0.0

      # Deduplicate and rank recommendations by frequency
      rec_counts: dict[str, int] = {}
      for r in all_recs:
        rec_counts[r] = rec_counts.get(r, 0) + 1
      result["top_recommendations"] = sorted(
        rec_counts.keys(), key=lambda x: rec_counts[x], reverse=True,
      )[:10]
      result["critical_issues"] = list(set(all_critical))[:10]

    except Exception as exc:
      logger.warning("[ImprovementFeed] Cross-audit query failed: %s", exc)
    return result

  # ── Section 4: Trade Decision Accuracy ─────────────────────────

  def _query_trade_accuracy(self) -> dict[str, Any]:
    """Analyze trade decision outcomes."""
    conn = get_db()
    result: dict[str, Any] = {
      "total_decisions": 0,
      "by_action": {},
      "by_status": {},
      "confidence_calibration": [],
      "recent_decisions": [],
    }
    try:
      # Count by action type
      rows = conn.execute(
        """
        SELECT action, COUNT(*), AVG(confidence)
        FROM trade_decisions
        WHERE ts >= ?
        GROUP BY action
        """,
        [self._cutoff],
      ).fetchall()
      for row in rows:
        result["by_action"][row[0]] = {
          "count": row[1],
          "avg_confidence": round(row[2] or 0, 2),
        }
        result["total_decisions"] += row[1]

      # Count by status
      rows = conn.execute(
        """
        SELECT status, COUNT(*)
        FROM trade_decisions
        WHERE ts >= ?
        GROUP BY status
        """,
        [self._cutoff],
      ).fetchall()
      for row in rows:
        result["by_status"][row[0]] = row[1]

      # Recent decisions with rejections
      rows = conn.execute(
        """
        SELECT symbol, action, confidence, status,
               rejection_reason, rationale, ts
        FROM trade_decisions
        WHERE ts >= ?
        ORDER BY ts DESC
        LIMIT 20
        """,
        [self._cutoff],
      ).fetchall()
      for row in rows:
        result["recent_decisions"].append({
          "symbol": row[0],
          "action": row[1],
          "confidence": round(row[2] or 0, 2),
          "status": row[3],
          "rejection": (row[4] or "")[:200],
          "rationale": (row[5] or "")[:200],
          "ts": str(row[6]),
        })

      # Confidence calibration: group by confidence buckets
      rows = conn.execute(
        """
        SELECT
          CASE
            WHEN confidence >= 0.8 THEN 'high (0.8+)'
            WHEN confidence >= 0.6 THEN 'medium (0.6-0.8)'
            ELSE 'low (<0.6)'
          END as bucket,
          COUNT(*),
          SUM(CASE WHEN status = 'executed' THEN 1 ELSE 0 END),
          SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END)
        FROM trade_decisions
        WHERE ts >= ?
        GROUP BY bucket
        """,
        [self._cutoff],
      ).fetchall()
      for row in rows:
        result["confidence_calibration"].append({
          "bucket": row[0],
          "total": row[1],
          "executed": row[2] or 0,
          "rejected": row[3] or 0,
        })
    except Exception as exc:
      logger.warning("[ImprovementFeed] Trade accuracy query failed: %s", exc)
    return result

  # ── Section 5: Data Completeness Gaps ──────────────────────────

  def _query_data_gaps(self) -> dict[str, Any]:
    """Scan recent strategist audit reports for data gaps."""
    result: dict[str, Any] = {
      "gap_counts": {},
      "tickers_with_gaps": [],
      "latest_report": "",
    }
    try:
      # Find the latest strategist audit report
      audit_files = sorted(
        REPORTS_DIR.glob("strategist_audit_*.md"),
        reverse=True,
      )
      if audit_files:
        latest = audit_files[0]
        result["latest_report"] = str(latest.name)
        content = latest.read_text(encoding="utf-8")

        # Parse the Data Completeness section
        in_gaps_section = False
        for line in content.split("\n"):
          if "Data Completeness" in line:
            in_gaps_section = True
            continue
          if in_gaps_section and line.startswith("## "):
            break
          if in_gaps_section and "|" in line and "Ticker" not in line and "---" not in line:
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) >= 2:
              ticker = parts[0]
              gaps = parts[1]
              result["tickers_with_gaps"].append({
                "ticker": ticker,
                "gaps": gaps,
              })
              # Count each gap type
              for gap in gaps.split(","):
                gap = gap.strip()
                if gap:
                  result["gap_counts"][gap] = result["gap_counts"].get(gap, 0) + 1
    except Exception as exc:
      logger.warning("[ImprovementFeed] Data gaps scan failed: %s", exc)
    return result

  # ── Section 6: Portfolio Performance ───────────────────────────

  def _query_portfolio_stats(self) -> dict[str, Any]:
    """Get portfolio snapshot trends."""
    conn = get_db()
    result: dict[str, Any] = {
      "bots": [],
      "total_pnl": 0.0,
    }
    try:
      rows = conn.execute(
        """
        SELECT bot_id,
               MIN(total_portfolio_value) as min_val,
               MAX(total_portfolio_value) as max_val,
               COUNT(*) as snapshots
        FROM portfolio_snapshots
        WHERE timestamp >= ?
        GROUP BY bot_id
        """,
        [self._cutoff],
      ).fetchall()
      for row in rows:
        bot_id, min_val, max_val, snaps = row
        pnl = (max_val or 0) - (min_val or 0)
        result["bots"].append({
          "bot_id": bot_id,
          "min_value": round(min_val or 0, 2),
          "max_value": round(max_val or 0, 2),
          "pnl": round(pnl, 2),
          "snapshots": snaps,
        })
        result["total_pnl"] += pnl
      result["total_pnl"] = round(result["total_pnl"], 2)
    except Exception as exc:
      logger.warning("[ImprovementFeed] Portfolio stats query failed: %s", exc)
    return result

  # ── Priority Queue Generator ───────────────────────────────────

  def _build_priority_queue(
    self,
    errors: dict,
    quality: dict,
    audits: dict,
    trades: dict,
    gaps: dict,
  ) -> list[dict]:
    """Synthesize all sections into a ranked priority queue."""
    items: list[dict] = []

    # Critical: pipeline errors
    if errors.get("pipeline_errors"):
      error_count = len(errors["pipeline_errors"])
      items.append({
        "severity": "CRITICAL",
        "category": "Pipeline",
        "issue": f"{error_count} pipeline error(s) in last {self._lookback_hours}h",
        "fix_location": "Check reports/health_*.md for stack traces",
        "detail": "; ".join(
          e["detail"][:80] for e in errors["pipeline_errors"][:3]
        ),
      })

    # Critical: high LLM failure rate
    total = errors.get("llm_total_calls", 0)
    json_fails = errors.get("llm_json_parse_failures", 0)
    if total > 0:
      fail_rate = json_fails / total
      if fail_rate > 0.1:
        items.append({
          "severity": "CRITICAL",
          "category": "LLM Quality",
          "issue": (
            f"JSON parse failure rate: {fail_rate:.0%} "
            f"({json_fails}/{total} calls)"
          ),
          "fix_location": "LLM prompts in portfolio_strategist.py, trading_agent.py",
          "detail": "Models are producing non-JSON responses too often",
        })

    # High: LLM timeouts
    timeouts = errors.get("llm_timeouts", 0)
    if timeouts > 0:
      items.append({
        "severity": "HIGH",
        "category": "LLM Performance",
        "issue": f"{timeouts} LLM call(s) timed out (>120s)",
        "fix_location": "llm_service.py timeout settings, context size",
        "detail": "Consider reducing context length or prompt size",
      })

    # High: low cross-audit score
    avg_audit = audits.get("avg_score", 0)
    if avg_audit > 0 and avg_audit < 6.0:
      items.append({
        "severity": "HIGH",
        "category": "Cross-Audit",
        "issue": f"Average audit score: {avg_audit}/10 (below 6.0 threshold)",
        "fix_location": "Prompts and data pipelines per audit recommendations",
        "detail": "; ".join(audits.get("top_recommendations", [])[:3]),
      })

    # High: critical issues from audits
    for issue in audits.get("critical_issues", [])[:3]:
      items.append({
        "severity": "HIGH",
        "category": "Cross-Audit",
        "issue": issue[:120],
        "fix_location": "See audit recommendations",
        "detail": "",
      })

    # Medium: data completeness gaps
    gap_counts = gaps.get("gap_counts", {})
    if gap_counts:
      top_gap = max(gap_counts.items(), key=lambda x: x[1])
      items.append({
        "severity": "MEDIUM",
        "category": "Data Gaps",
        "issue": (
          f"Most common gap: '{top_gap[0]}' "
          f"({top_gap[1]} tickers affected)"
        ),
        "fix_location": "data_distiller.py, yfinance_service.py data collectors",
        "detail": f"{len(gap_counts)} distinct gap types across "
                  f"{len(gaps.get('tickers_with_gaps', []))} tickers",
      })

    # Medium: too many HOLD decisions (indecisive)
    hold_count = trades.get("by_action", {}).get("HOLD", {}).get("count", 0)
    buy_count = trades.get("by_action", {}).get("BUY", {}).get("count", 0)
    sell_count = trades.get("by_action", {}).get("SELL", {}).get("count", 0)
    total_decisions = hold_count + buy_count + sell_count
    if total_decisions > 0 and hold_count / total_decisions > 0.7:
      items.append({
        "severity": "MEDIUM",
        "category": "Trading",
        "issue": (
          f"Bot is too indecisive: {hold_count}/{total_decisions} "
          f"decisions were HOLD ({hold_count / total_decisions:.0%})"
        ),
        "fix_location": "portfolio_strategist.py trading prompts, thresholds",
        "detail": "Consider lowering conviction threshold or adjusting prompts",
      })

    # Medium: rejected trades
    rejected = trades.get("by_status", {}).get("rejected", 0)
    if rejected > 3:
      items.append({
        "severity": "MEDIUM",
        "category": "Risk Rules",
        "issue": f"{rejected} trades rejected by risk rules",
        "fix_location": "risk_rules.py, risk_service.py",
        "detail": "Review if risk rules are too strict or if trades are genuinely risky",
      })

    # Low: slow LLM steps
    for step, stats in quality.get("steps", {}).items():
      if stats.get("avg_latency_ms", 0) > 60_000:
        items.append({
          "severity": "LOW",
          "category": "Performance",
          "issue": (
            f"LLM step '{step}' avg latency: "
            f"{stats['avg_latency_ms'] / 1000:.1f}s"
          ),
          "fix_location": f"Prompt for '{step}' step — consider shortening",
          "detail": f"{stats['total_calls']} calls, {stats['total_tokens']} total tokens",
        })

    # Sort by severity
    severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    items.sort(key=lambda x: severity_order.get(x["severity"], 99))

    return items

  # ── Report Generator ───────────────────────────────────────────

  def generate_report(self, lookback_hours: int | None = None) -> str:
    """Generate the improvement feed report.

    Returns the absolute path to the generated report file.
    """
    if lookback_hours is not None:
      self._lookback_hours = lookback_hours
      self._cutoff = datetime.now() - timedelta(hours=lookback_hours)

    now = datetime.now()
    logger.info(
      "[ImprovementFeed] Generating report (lookback=%dh)",
      self._lookback_hours,
    )

    # Query all sections
    errors = self._query_pipeline_errors()
    quality = self._query_llm_quality()
    audits = self._query_cross_audits()
    trades = self._query_trade_accuracy()
    gaps = self._query_data_gaps()
    portfolio = self._query_portfolio_stats()

    # Build priority queue
    priority = self._build_priority_queue(
      errors, quality, audits, trades, gaps,
    )

    # ── Render Markdown ──────────────────────────────────────────
    lines: list[str] = []
    lines.append(
      f"# Improvement Feed — {now.strftime('%Y-%m-%d %H:%M:%S')}"
    )
    lines.append("")
    lines.append(
      f"**Lookback:** {self._lookback_hours} hours  "
    )
    lines.append(
      f"**Generated:** {now.isoformat()}"
    )
    lines.append("")

    # ── Priority Queue ───────────────────────────────────────
    lines.append("## Priority Queue (What To Fix Next)")
    lines.append("")
    if priority:
      lines.append("| # | Severity | Category | Issue | Fix Location |")
      lines.append("|---|----------|----------|-------|-------------|")
      for i, item in enumerate(priority, 1):
        sev = item["severity"]
        icon = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🔵"}.get(sev, "⚪")
        lines.append(
          f"| {i} | {icon} {sev} | {item['category']} | "
          f"{item['issue'][:100]} | `{item['fix_location'][:60]}` |"
        )
      lines.append("")
      # Detail section
      lines.append("### Priority Details")
      lines.append("")
      for i, item in enumerate(priority, 1):
        if item.get("detail"):
          lines.append(f"**#{i}:** {item['detail']}")
          lines.append("")
    else:
      lines.append("✅ No issues detected — pipeline is healthy!")
      lines.append("")

    # ── Section 1: Pipeline Errors ───────────────────────────
    lines.append("## Section 1: Pipeline Errors")
    lines.append("")
    total_calls = errors.get("llm_total_calls", 0)
    json_fails = errors.get("llm_json_parse_failures", 0)
    timeouts = errors.get("llm_timeouts", 0)
    lines.append(f"- **Total LLM calls:** {total_calls}")
    lines.append(
      f"- **JSON parse failures:** {json_fails} "
      f"({json_fails / total_calls * 100:.1f}%)" if total_calls else
      f"- **JSON parse failures:** {json_fails}"
    )
    lines.append(f"- **Timeouts (>120s):** {timeouts}")
    lines.append(
      f"- **Pipeline errors:** {len(errors.get('pipeline_errors', []))}"
    )
    lines.append("")

    if errors.get("llm_failures"):
      lines.append("### Recent LLM Failures")
      lines.append("")
      for f in errors["llm_failures"][:5]:
        lines.append(f"- **{f['step']}** ({f['model']}): `{f['preview'][:100]}`")
      lines.append("")

    if errors.get("pipeline_errors"):
      lines.append("### Recent Pipeline Errors")
      lines.append("")
      for e in errors["pipeline_errors"][:5]:
        lines.append(
          f"- [{e['phase']}] {e['event_type']}: {e['detail'][:150]}"
        )
      lines.append("")

    # ── Section 2: LLM Quality Scorecard ─────────────────────
    lines.append("## Section 2: LLM Quality Scorecard")
    lines.append("")
    overall = quality.get("overall", {})
    lines.append(
      f"- **Total calls:** {overall.get('total_calls', 0)}"
    )
    lines.append(
      f"- **Avg latency:** {overall.get('avg_latency_ms', 0)}ms"
    )
    lines.append(
      f"- **Total tokens:** {overall.get('total_tokens', 0):,}"
    )
    lines.append("")

    if quality.get("steps"):
      lines.append("| Step | Calls | Avg Latency | Avg Tokens |")
      lines.append("|------|-------|-------------|------------|")
      for step, stats in sorted(
        quality["steps"].items(),
        key=lambda x: x[1]["total_calls"],
        reverse=True,
      ):
        lines.append(
          f"| {step} | {stats['total_calls']} | "
          f"{stats['avg_latency_ms']}ms | {stats['avg_tokens']} |"
        )
      lines.append("")

    # ── Section 3: Cross-Model Consistency ────────────────────
    lines.append("## Section 3: Cross-Model Consistency")
    lines.append("")
    lines.append(f"- **Average audit score:** {audits.get('avg_score', 0)}/10")
    lines.append(f"- **Audits performed:** {len(audits.get('audits', []))}")
    lines.append("")

    if audits.get("audits"):
      lines.append("| Audited Bot | Auditor | Score | Weakest Category |")
      lines.append("|-------------|---------|-------|-----------------|")
      for a in audits["audits"][:5]:
        cats = a.get("categories", {})
        weakest = "N/A"
        if cats:
          weakest_cat = min(
            cats.items(),
            key=lambda x: x[1].get("score", 10) if isinstance(x[1], dict) else 10,
          )
          if isinstance(weakest_cat[1], dict):
            weakest = f"{weakest_cat[0]} ({weakest_cat[1].get('score', '?')}/10)"
          else:
            weakest = weakest_cat[0]
        lines.append(
          f"| {a['audited'][:20]} | {a['auditor'][:20]} | "
          f"{a['score']}/10 | {weakest} |"
        )
      lines.append("")

    if audits.get("top_recommendations"):
      lines.append("### Top Recommendations (from auditors)")
      lines.append("")
      for r in audits["top_recommendations"][:5]:
        lines.append(f"- {r}")
      lines.append("")

    # ── Section 4: Trade Decision Accuracy ────────────────────
    lines.append("## Section 4: Trade Decision Accuracy")
    lines.append("")
    lines.append(
      f"- **Total decisions:** {trades.get('total_decisions', 0)}"
    )

    by_action = trades.get("by_action", {})
    if by_action:
      action_str = ", ".join(
        f"{k}: {v['count']} (avg conf={v['avg_confidence']})"
        for k, v in by_action.items()
      )
      lines.append(f"- **By action:** {action_str}")

    by_status = trades.get("by_status", {})
    if by_status:
      status_str = ", ".join(f"{k}: {v}" for k, v in by_status.items())
      lines.append(f"- **By status:** {status_str}")
    lines.append("")

    if trades.get("confidence_calibration"):
      lines.append("### Confidence Calibration")
      lines.append("")
      lines.append("| Confidence Bucket | Total | Executed | Rejected |")
      lines.append("|-------------------|-------|----------|----------|")
      for cal in trades["confidence_calibration"]:
        lines.append(
          f"| {cal['bucket']} | {cal['total']} | "
          f"{cal['executed']} | {cal['rejected']} |"
        )
      lines.append("")

    # ── Section 5: Data Completeness Gaps ─────────────────────
    lines.append("## Section 5: Data Completeness Gaps")
    lines.append("")
    gap_counts = gaps.get("gap_counts", {})
    if gap_counts:
      lines.append("| Gap Type | Tickers Affected |")
      lines.append("|----------|-----------------|")
      for gap_type, count in sorted(
        gap_counts.items(), key=lambda x: x[1], reverse=True,
      )[:10]:
        lines.append(f"| {gap_type} | {count} |")
      lines.append("")
    else:
      lines.append("✅ No data gaps detected.")
      lines.append("")

    # ── Section 6: Portfolio Performance ──────────────────────
    lines.append("## Section 6: Portfolio Performance")
    lines.append("")
    lines.append(f"- **Total P&L:** ${portfolio.get('total_pnl', 0):.2f}")
    lines.append("")

    if portfolio.get("bots"):
      lines.append("| Bot | Min Value | Max Value | P&L |")
      lines.append("|-----|-----------|-----------|-----|")
      for b in portfolio["bots"]:
        lines.append(
          f"| {b['bot_id'][:20]} | ${b['min_value']:.2f} | "
          f"${b['max_value']:.2f} | ${b['pnl']:.2f} |"
        )
      lines.append("")

    # ── Write to disk ────────────────────────────────────────
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"improvement_feed_{now.strftime('%Y-%m-%d_%H%M%S')}.md"
    report_path = REPORTS_DIR / filename
    report_text = "\n".join(lines)
    report_path.write_text(report_text, encoding="utf-8")

    # Prune old feeds (keep 10)
    try:
      from app.utils.logger import prune_old_files
      prune_old_files(REPORTS_DIR, "improvement_feed_*.md")
    except Exception:
      pass

    logger.info("[ImprovementFeed] Report written to %s", report_path)
    return str(report_path)

  # ── Quick summary for API ──────────────────────────────────────

  def get_latest_report_path(self) -> str | None:
    """Return the path to the most recent improvement feed report."""
    try:
      files = sorted(
        REPORTS_DIR.glob("improvement_feed_*.md"),
        reverse=True,
      )
      return str(files[0]) if files else None
    except Exception:
      return None

  def get_latest_report_content(self) -> str:
    """Return the content of the most recent improvement feed report."""
    path = self.get_latest_report_path()
    if path:
      return Path(path).read_text(encoding="utf-8")
    return "No improvement feed reports found. Run a pipeline cycle first."

  # ── Benchmark Stats Persistence ────────────────────────────────

  def record_benchmark_stats(
    self,
    cycle_id: str = "",
    bot_id: str = "default",
  ) -> dict[str, Any]:
    """Record per-cycle stats to benchmark_stats table for trend tracking.

    Returns the stats dict that was recorded.
    """
    import uuid

    errors = self._query_pipeline_errors()
    quality = self._query_llm_quality()
    audits = self._query_cross_audits()
    trades = self._query_trade_accuracy()
    portfolio = self._query_portfolio_stats()

    total_calls = errors.get("llm_total_calls", 0)
    json_fails = errors.get("llm_json_parse_failures", 0)
    success_rate = (
      (total_calls - json_fails) / total_calls
      if total_calls > 0 else 0.0
    )

    # Calculate trade accuracy (executed / total decisions)
    total_decisions = trades.get("total_decisions", 0)
    executed = trades.get("by_status", {}).get("executed", 0)
    trade_accuracy = (
      executed / total_decisions if total_decisions > 0 else 0.0
    )

    stats = {
      "id": str(uuid.uuid4())[:8],
      "cycle_id": cycle_id,
      "bot_id": bot_id,
      "json_parse_success_rate": round(success_rate, 4),
      "trade_accuracy": round(trade_accuracy, 4),
      "avg_llm_latency_ms": quality.get("overall", {}).get("avg_latency_ms", 0),
      "data_completeness": 0.0,  # TODO: compute from gaps
      "cross_audit_score": audits.get("avg_score", 0.0),
      "total_errors": len(errors.get("pipeline_errors", [])),
      "total_warnings": 0,
      "total_llm_calls": total_calls,
      "total_tokens_used": quality.get("overall", {}).get("total_tokens", 0),
      "decisions_made": total_decisions,
      "trades_executed": executed,
      "trades_rejected": trades.get("by_status", {}).get("rejected", 0),
      "portfolio_pnl": portfolio.get("total_pnl", 0.0),
    }

    try:
      conn = get_db()
      conn.execute(
        """
        INSERT INTO benchmark_stats (
          id, cycle_id, bot_id, json_parse_success_rate,
          trade_accuracy, avg_llm_latency_ms, data_completeness,
          cross_audit_score, total_errors, total_warnings,
          total_llm_calls, total_tokens_used, decisions_made,
          trades_executed, trades_rejected, portfolio_pnl
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
          stats["id"], stats["cycle_id"], stats["bot_id"],
          stats["json_parse_success_rate"], stats["trade_accuracy"],
          stats["avg_llm_latency_ms"], stats["data_completeness"],
          stats["cross_audit_score"], stats["total_errors"],
          stats["total_warnings"], stats["total_llm_calls"],
          stats["total_tokens_used"], stats["decisions_made"],
          stats["trades_executed"], stats["trades_rejected"],
          stats["portfolio_pnl"],
        ],
      )
      conn.commit()
      logger.info("[ImprovementFeed] Benchmark stats recorded: %s", stats["id"])
    except Exception as exc:
      logger.warning("[ImprovementFeed] Failed to record stats: %s", exc)

    return stats

