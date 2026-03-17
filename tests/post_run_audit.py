#!/usr/bin/env python3
"""
Post-Run Audit Tests
====================
Run this AFTER a trading cycle completes to verify the entire pipeline
operated correctly — conversations tracked, audit logs saved, provider
info recorded, workflows generated, no data gaps.

Usage:
    source venv/bin/activate
    python tests/post_run_audit.py [--cycle-id <ID>]

If no --cycle-id is given, audits the MOST RECENT cycle.
"""

import argparse
import json
import sys
import os
import traceback

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import duckdb
from app.config import settings

# ──────────────────────────────────────────────────────────────────
# Test Infrastructure
# ──────────────────────────────────────────────────────────────────

class AuditResult:
    """Holds the result of a single audit check."""
    def __init__(self, name, passed, details="", severity="ERROR"):
        self.name = name
        self.passed = passed
        self.details = details
        self.severity = severity  # ERROR, WARNING, INFO

RESULTS = []

def audit(name, severity="ERROR"):
    """Decorator to register an audit check."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            try:
                passed, details = func(*args, **kwargs)
                RESULTS.append(AuditResult(name, passed, details, severity))
            except Exception as e:
                RESULTS.append(AuditResult(name, False, f"Exception: {e}\n{traceback.format_exc()}", severity))
        wrapper._audit_name = name
        return wrapper
    return decorator


def get_latest_cycle_id(conn):
    """Get the most recent cycle_id from audit logs."""
    rows = conn.execute("""
        SELECT DISTINCT cycle_id FROM llm_audit_logs
        WHERE cycle_id != '' AND cycle_id IS NOT NULL
        ORDER BY created_at DESC LIMIT 1
    """).fetchall()
    return rows[0][0] if rows else None


# ──────────────────────────────────────────────────────────────────
# Part 1: Data Integrity Checks
# ──────────────────────────────────────────────────────────────────

@audit("1.1 Audit Logs Exist")
def check_audit_logs_exist(conn, cycle_id):
    """Verify that audit log rows were created for this cycle."""
    count = conn.execute(
        "SELECT count(*) FROM llm_audit_logs WHERE cycle_id = ?", [cycle_id]
    ).fetchone()[0]
    if count == 0:
        return False, f"No audit logs found for cycle_id={cycle_id}"
    return True, f"{count} audit log entries found for cycle {cycle_id}"


@audit("1.2 All Logs Have Provider Field")
def check_provider_field(conn, cycle_id):
    """Every audit log row should have a non-empty provider."""
    total = conn.execute(
        "SELECT count(*) FROM llm_audit_logs WHERE cycle_id = ?", [cycle_id]
    ).fetchone()[0]
    missing = conn.execute(
        "SELECT count(*) FROM llm_audit_logs WHERE cycle_id = ? AND (provider IS NULL OR provider = '')",
        [cycle_id]
    ).fetchone()[0]
    if missing > 0:
        return False, f"{missing}/{total} logs missing provider field"
    providers = conn.execute(
        "SELECT DISTINCT provider FROM llm_audit_logs WHERE cycle_id = ?", [cycle_id]
    ).fetchall()
    provider_list = [p[0] for p in providers]
    return True, f"All {total} logs have provider set. Providers used: {provider_list}"


@audit("1.3 All Logs Have Conversation ID")
def check_conversation_id(conn, cycle_id):
    """Every audit log should be linked to a conversation."""
    total = conn.execute(
        "SELECT count(*) FROM llm_audit_logs WHERE cycle_id = ?", [cycle_id]
    ).fetchone()[0]
    missing = conn.execute(
        "SELECT count(*) FROM llm_audit_logs WHERE cycle_id = ? AND (conversation_id IS NULL OR conversation_id = '')",
        [cycle_id]
    ).fetchone()[0]
    if missing > 0:
        return False, f"{missing}/{total} logs missing conversation_id"
    return True, f"All {total} logs linked to conversations"


@audit("1.4 All Logs Have Model Name")
def check_model_field(conn, cycle_id):
    """Every log should record which model was used."""
    missing = conn.execute(
        "SELECT count(*) FROM llm_audit_logs WHERE cycle_id = ? AND (model IS NULL OR model = '')",
        [cycle_id]
    ).fetchone()[0]
    if missing > 0:
        return False, f"{missing} logs missing model name"
    models = conn.execute(
        "SELECT DISTINCT model FROM llm_audit_logs WHERE cycle_id = ?", [cycle_id]
    ).fetchall()
    return True, f"Models used: {[m[0] for m in models]}"


@audit("1.5 All Logs Have Token Count > 0")
def check_tokens(conn, cycle_id):
    """Every LLM call should produce some tokens."""
    zero_tok = conn.execute(
        "SELECT count(*) FROM llm_audit_logs WHERE cycle_id = ? AND (tokens_used IS NULL OR tokens_used = 0)",
        [cycle_id]
    ).fetchone()[0]
    total_tok = conn.execute(
        "SELECT SUM(tokens_used) FROM llm_audit_logs WHERE cycle_id = ?", [cycle_id]
    ).fetchone()[0] or 0
    if zero_tok > 0:
        return False, f"{zero_tok} logs have tokens_used=0 (total tokens: {total_tok})"
    return True, f"Total tokens consumed: {total_tok:,}"


@audit("1.6 All Logs Have Execution Time > 0")
def check_execution_time(conn, cycle_id):
    """Every LLM call should have a measured execution time."""
    zero_time = conn.execute(
        "SELECT count(*) FROM llm_audit_logs WHERE cycle_id = ? AND (execution_time_ms IS NULL OR execution_time_ms = 0)",
        [cycle_id]
    ).fetchone()[0]
    avg_time = conn.execute(
        "SELECT AVG(execution_time_ms) FROM llm_audit_logs WHERE cycle_id = ? AND execution_time_ms > 0",
        [cycle_id]
    ).fetchone()[0] or 0
    if zero_time > 0:
        return False, f"{zero_time} logs have execution_time_ms=0"
    return True, f"Avg execution time: {avg_time:.0f}ms"


@audit("1.7 All Logs Have Non-Empty Raw Response")
def check_raw_response(conn, cycle_id):
    """Every LLM call should have captured the raw response."""
    empty = conn.execute(
        "SELECT count(*) FROM llm_audit_logs WHERE cycle_id = ? AND (raw_response IS NULL OR raw_response = '')",
        [cycle_id]
    ).fetchone()[0]
    if empty > 0:
        steps = conn.execute(
            "SELECT agent_step FROM llm_audit_logs WHERE cycle_id = ? AND (raw_response IS NULL OR raw_response = '')",
            [cycle_id]
        ).fetchall()
        return False, f"{empty} logs have empty raw_response: {[s[0] for s in steps]}"
    return True, "All logs have raw_response captured"


@audit("1.8 All Logs Have Non-Empty System Prompt")
def check_system_prompt(conn, cycle_id):
    """Every LLM call should have a system prompt recorded."""
    empty = conn.execute(
        "SELECT count(*) FROM llm_audit_logs WHERE cycle_id = ? AND (system_prompt IS NULL OR system_prompt = '')",
        [cycle_id]
    ).fetchone()[0]
    if empty > 0:
        return False, f"{empty} logs have empty system_prompt"
    return True, "All logs have system_prompt captured"


# ──────────────────────────────────────────────────────────────────
# Part 2: Pipeline Step Completeness
# ──────────────────────────────────────────────────────────────────

@audit("2.1 Pipeline Contains Expected Agent Steps")
def check_pipeline_steps(conn, cycle_id):
    """Verify critical pipeline steps were executed."""
    steps = conn.execute(
        "SELECT DISTINCT agent_step FROM llm_audit_logs WHERE cycle_id = ?", [cycle_id]
    ).fetchall()
    step_names = {s[0] for s in steps}

    # These are the core steps that should appear in a full pipeline run
    critical = {"agentic_extract", "agentic_summarize"}
    # These are deeper analysis steps (may not all appear depending on config)
    optional_deep = {"agentic_self_question", "contradiction_pass", "final_decision"}
    # Analyst steps are dynamic (analyst_fundamental, analyst_technical, etc.)
    analyst_steps = {s for s in step_names if s.startswith("analyst_")}
    thesis_steps = {s for s in step_names if s.startswith("thesis_synthesis")}

    missing_critical = critical - step_names
    if missing_critical:
        return False, f"Missing critical steps: {missing_critical}. Found: {sorted(step_names)}"

    details = [
        f"Steps found: {sorted(step_names)}",
        f"Analyst domains: {sorted(analyst_steps) if analyst_steps else 'none'}",
        f"Thesis synthesis: {sorted(thesis_steps) if thesis_steps else 'none'}",
        f"Has final_decision: {'final_decision' in step_names}",
    ]
    return True, "\n    ".join(details)


@audit("2.2 Agent Steps Execute In Correct Order", severity="WARNING")
def check_step_ordering(conn, cycle_id):
    """Verify agentic_extract happens before agentic_summarize."""
    rows = conn.execute("""
        SELECT agent_step, MIN(created_at) as first_at
        FROM llm_audit_logs
        WHERE cycle_id = ? AND agent_step IN ('agentic_extract', 'agentic_summarize', 'final_decision')
        GROUP BY agent_step
        ORDER BY first_at
    """, [cycle_id]).fetchall()

    if len(rows) < 2:
        return True, f"Only {len(rows)} steps found, ordering check not applicable"

    order = [r[0] for r in rows]
    expected_pairs = [("agentic_extract", "agentic_summarize")]
    for before, after in expected_pairs:
        if before in order and after in order:
            if order.index(before) > order.index(after):
                return False, f"{before} ran AFTER {after} — wrong order! Order: {order}"

    return True, f"Execution order: {order}"


# ──────────────────────────────────────────────────────────────────
# Part 3: Conversation Tracking
# ──────────────────────────────────────────────────────────────────

@audit("3.1 Conversations Were Created")
def check_conversations_exist(conn, cycle_id):
    """Verify conversation records exist for this cycle."""
    count = conn.execute(
        "SELECT count(*) FROM llm_conversations WHERE cycle_id = ?", [cycle_id]
    ).fetchone()[0]
    if count == 0:
        return False, f"No conversations found for cycle {cycle_id}"
    return True, f"{count} conversations recorded"


@audit("3.2 All Conversations Completed (Not Stuck Active)")
def check_conversations_completed(conn, cycle_id):
    """All conversations should have status='completed', not 'active'."""
    active = conn.execute(
        "SELECT count(*) FROM llm_conversations WHERE cycle_id = ? AND status = 'active'",
        [cycle_id]
    ).fetchone()[0]
    if active > 0:
        stuck = conn.execute(
            "SELECT id, title, agent_step, created_at FROM llm_conversations WHERE cycle_id = ? AND status = 'active'",
            [cycle_id]
        ).fetchall()
        return False, f"{active} conversations still 'active' (stuck): {stuck}"
    return True, "All conversations completed cleanly"


@audit("3.3 Conversations Have Token Counts")
def check_conversation_tokens(conn, cycle_id):
    """Every conversation should have tokens recorded."""
    zero_tok = conn.execute(
        "SELECT count(*) FROM llm_conversations WHERE cycle_id = ? AND (total_tokens IS NULL OR total_tokens = 0)",
        [cycle_id]
    ).fetchone()[0]
    if zero_tok > 0:
        return False, f"{zero_tok} conversations have total_tokens=0"
    total = conn.execute(
        "SELECT SUM(total_tokens) FROM llm_conversations WHERE cycle_id = ?", [cycle_id]
    ).fetchone()[0] or 0
    return True, f"Total conversation tokens: {total:,}"


@audit("3.4 Conversations Have Provider Set")
def check_conversation_provider(conn, cycle_id):
    """Every conversation should identify its provider."""
    missing = conn.execute(
        "SELECT count(*) FROM llm_conversations WHERE cycle_id = ? AND (provider IS NULL OR provider = '')",
        [cycle_id]
    ).fetchone()[0]
    if missing > 0:
        return False, f"{missing} conversations missing provider"
    providers = conn.execute(
        "SELECT DISTINCT provider FROM llm_conversations WHERE cycle_id = ?", [cycle_id]
    ).fetchall()
    return True, f"Providers: {[p[0] for p in providers]}"


@audit("3.5 Conversations Have Tok/s Calculated")
def check_conversation_tps(conn, cycle_id):
    """Verify tokens_per_second was calculated for conversations."""
    zero_tps = conn.execute(
        "SELECT count(*) FROM llm_conversations WHERE cycle_id = ? AND (tokens_per_second IS NULL OR tokens_per_second = 0)",
        [cycle_id]
    ).fetchone()[0]
    total = conn.execute(
        "SELECT count(*) FROM llm_conversations WHERE cycle_id = ?", [cycle_id]
    ).fetchone()[0]
    if zero_tps > 0:
        return False, f"{zero_tps}/{total} conversations have tokens_per_second=0"
    avg_tps = conn.execute(
        "SELECT AVG(tokens_per_second) FROM llm_conversations WHERE cycle_id = ? AND tokens_per_second > 0",
        [cycle_id]
    ).fetchone()[0] or 0
    return True, f"Avg tokens/sec: {avg_tps:.1f}"


# ──────────────────────────────────────────────────────────────────
# Part 4: Workflow Generation
# ──────────────────────────────────────────────────────────────────

@audit("4.1 Workflow Was Generated")
def check_workflow_exists(conn, cycle_id):
    """A pipeline_workflows record should exist for this cycle."""
    count = conn.execute(
        "SELECT count(*) FROM pipeline_workflows WHERE cycle_id = ?", [cycle_id]
    ).fetchone()[0]
    if count == 0:
        return False, f"No workflow generated for cycle {cycle_id}"
    row = conn.execute(
        "SELECT node_count, connection_count, total_tokens, total_duration_ms FROM pipeline_workflows WHERE cycle_id = ?",
        [cycle_id]
    ).fetchone()
    return True, f"Workflow: {row[0]} nodes, {row[1]} connections, {row[2]} tokens, {row[3]}ms"


@audit("4.2 Workflow Has Nodes and Connections", severity="WARNING")
def check_workflow_structure(conn, cycle_id):
    """Workflow should have a reasonable number of nodes."""
    row = conn.execute(
        "SELECT node_count, connection_count, workflow_json FROM pipeline_workflows WHERE cycle_id = ? LIMIT 1",
        [cycle_id]
    ).fetchone()
    if not row:
        return False, "No workflow found"
    node_count, conn_count, wf_json = row
    if node_count == 0:
        return False, "Workflow has 0 nodes"

    details = [f"Nodes: {node_count}, Connections: {conn_count}"]
    if wf_json:
        try:
            wf = json.loads(wf_json)
            if "nodes" in wf:
                node_types = [n.get("type", "?") for n in wf["nodes"]]
                details.append(f"Node types: {node_types}")
        except:
            details.append("Could not parse workflow JSON")
    return True, "\n    ".join(details)


# ──────────────────────────────────────────────────────────────────
# Part 5: Data Quality / Sanity Checks
# ──────────────────────────────────────────────────────────────────

@audit("5.1 No Duplicate Audit Log IDs")
def check_no_duplicate_ids(conn, cycle_id):
    """Log IDs should be unique."""
    dupes = conn.execute("""
        SELECT id, count(*) as cnt FROM llm_audit_logs
        WHERE cycle_id = ?
        GROUP BY id HAVING cnt > 1
    """, [cycle_id]).fetchall()
    if dupes:
        return False, f"Duplicate log IDs found: {dupes}"
    return True, "All log IDs unique"


@audit("5.2 JSON Parsing Success Rate")
def check_json_parse_rate(conn, cycle_id):
    """Check how many responses were successfully parsed to JSON."""
    total = conn.execute(
        "SELECT count(*) FROM llm_audit_logs WHERE cycle_id = ?", [cycle_id]
    ).fetchone()[0]
    parsed = conn.execute(
        "SELECT count(*) FROM llm_audit_logs WHERE cycle_id = ? AND parsed_json IS NOT NULL AND parsed_json != ''",
        [cycle_id]
    ).fetchone()[0]
    # Not all steps produce JSON (extractions produce text), so this is a warning
    rate = (parsed / total * 100) if total > 0 else 0
    details = f"{parsed}/{total} logs have parsed_json ({rate:.0f}%)"
    if parsed == 0:
        return False, details
    return True, details


@audit("5.3 Reasoning Content Captured (Thinking Models)", severity="INFO")
def check_reasoning_content(conn, cycle_id):
    """For thinking models like QwQ, check reasoning_content is saved."""
    with_reasoning = conn.execute(
        "SELECT count(*) FROM llm_audit_logs WHERE cycle_id = ? AND reasoning_content IS NOT NULL AND reasoning_content != ''",
        [cycle_id]
    ).fetchone()[0]
    total = conn.execute(
        "SELECT count(*) FROM llm_audit_logs WHERE cycle_id = ?", [cycle_id]
    ).fetchone()[0]
    if with_reasoning > 0:
        avg_len = conn.execute(
            "SELECT AVG(LENGTH(reasoning_content)) FROM llm_audit_logs WHERE cycle_id = ? AND reasoning_content != ''",
            [cycle_id]
        ).fetchone()[0] or 0
        return True, f"{with_reasoning}/{total} logs have reasoning_content (avg {avg_len:.0f} chars)"
    return True, f"No reasoning_content captured (0/{total}). Model may not support thinking."


@audit("5.4 No Abnormally Slow Requests (>120s)", severity="WARNING")
def check_slow_requests(conn, cycle_id):
    """Flag any requests that took over 2 minutes — might indicate timeout issues."""
    slow = conn.execute("""
        SELECT agent_step, ticker, execution_time_ms, model
        FROM llm_audit_logs
        WHERE cycle_id = ? AND execution_time_ms > 120000
        ORDER BY execution_time_ms DESC
    """, [cycle_id]).fetchall()
    if slow:
        details = "\n    ".join([f"{r[0]} ({r[1]}): {r[2]/1000:.1f}s on {r[3]}" for r in slow])
        return False, f"{len(slow)} slow requests (>120s):\n    {details}"
    return True, "No requests exceeded 120s"


@audit("5.5 Tickers Were Processed")
def check_tickers_processed(conn, cycle_id):
    """Verify at least one ticker was analyzed."""
    tickers = conn.execute(
        "SELECT DISTINCT ticker FROM llm_audit_logs WHERE cycle_id = ? AND ticker IS NOT NULL AND ticker != ''",
        [cycle_id]
    ).fetchall()
    if not tickers:
        return False, "No tickers found in audit logs"
    ticker_list = sorted([t[0] for t in tickers])
    return True, f"Tickers processed: {ticker_list}"


# ──────────────────────────────────────────────────────────────────
# Part 6: Cross-Table Consistency
# ──────────────────────────────────────────────────────────────────

@audit("6.1 Audit Logs ↔ Conversations Cross-Reference")
def check_cross_reference(conn, cycle_id):
    """Verify audit logs reference conversations that actually exist."""
    orphan_convo_ids = conn.execute("""
        SELECT DISTINCT a.conversation_id
        FROM llm_audit_logs a
        LEFT JOIN llm_conversations c ON a.conversation_id = c.id
        WHERE a.cycle_id = ? AND a.conversation_id != '' AND c.id IS NULL
    """, [cycle_id]).fetchall()
    if orphan_convo_ids:
        return False, f"{len(orphan_convo_ids)} audit logs reference non-existent conversations: {orphan_convo_ids}"
    return True, "All audit log conversation_ids match existing conversation records"


@audit("6.2 Token Count Consistency (Logs vs Conversations)")
def check_token_consistency(conn, cycle_id):
    """Total tokens in audit logs should roughly match conversations total."""
    log_tokens = conn.execute(
        "SELECT SUM(tokens_used) FROM llm_audit_logs WHERE cycle_id = ?", [cycle_id]
    ).fetchone()[0] or 0
    convo_tokens = conn.execute(
        "SELECT SUM(total_tokens) FROM llm_conversations WHERE cycle_id = ?", [cycle_id]
    ).fetchone()[0] or 0
    if convo_tokens == 0 and log_tokens > 0:
        return False, f"Log tokens={log_tokens:,} but Conversation tokens=0 — tracking broken"
    if log_tokens == 0:
        return False, "Both log and conversation tokens are 0"
    ratio = convo_tokens / log_tokens if log_tokens > 0 else 0
    details = f"Audit log tokens: {log_tokens:,} | Conversation tokens: {convo_tokens:,} | Ratio: {ratio:.2f}"
    # Allow some variance (conversations may track slightly differently)
    if ratio < 0.5 or ratio > 2.0:
        return False, f"Token count mismatch — {details}"
    return True, details


# ──────────────────────────────────────────────────────────────────
# Runner
# ──────────────────────────────────────────────────────────────────

ALL_CHECKS = [
    # Part 1: Data Integrity
    check_audit_logs_exist,
    check_provider_field,
    check_conversation_id,
    check_model_field,
    check_tokens,
    check_execution_time,
    check_raw_response,
    check_system_prompt,
    # Part 2: Pipeline Steps
    check_pipeline_steps,
    check_step_ordering,
    # Part 3: Conversation Tracking
    check_conversations_exist,
    check_conversations_completed,
    check_conversation_tokens,
    check_conversation_provider,
    check_conversation_tps,
    # Part 4: Workflow
    check_workflow_exists,
    check_workflow_structure,
    # Part 5: Data Quality
    check_no_duplicate_ids,
    check_json_parse_rate,
    check_reasoning_content,
    check_slow_requests,
    check_tickers_processed,
    # Part 6: Consistency
    check_cross_reference,
    check_token_consistency,
]


def run_audit(cycle_id=None):
    """Run all audit checks and print a summary report."""
    conn = duckdb.connect(str(settings.DB_PATH), read_only=True)

    if not cycle_id:
        cycle_id = get_latest_cycle_id(conn)
        if not cycle_id:
            print("\n  ❌  No audit logs found in database. Run a trading cycle first.\n")
            return False

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║              POST-RUN PIPELINE AUDIT                        ║
║  cycle_id: {cycle_id[:50]:<50}║
╚══════════════════════════════════════════════════════════════╝
""")

    for check in ALL_CHECKS:
        check(conn, cycle_id)

    # Print results
    passed = sum(1 for r in RESULTS if r.passed)
    failed = sum(1 for r in RESULTS if not r.passed and r.severity == "ERROR")
    warns = sum(1 for r in RESULTS if not r.passed and r.severity == "WARNING")
    infos = sum(1 for r in RESULTS if r.severity == "INFO")
    total = len(RESULTS)

    for r in RESULTS:
        icon = "✅" if r.passed else ("⚠️ " if r.severity == "WARNING" else ("ℹ️ " if r.severity == "INFO" else "❌"))
        print(f"  {icon}  {r.name}")
        if r.details:
            for line in r.details.split("\n"):
                print(f"       {line}")
        print()

    print("─" * 62)
    print(f"  Results: {passed}/{total} passed | {failed} errors | {warns} warnings | {infos} info")
    print("─" * 62)

    if failed > 0:
        print("\n  ⛔  AUDIT FAILED — review errors above\n")
        return False
    elif warns > 0:
        print("\n  ⚠️  AUDIT PASSED WITH WARNINGS\n")
        return True
    else:
        print("\n  ✅  AUDIT PASSED — pipeline is healthy\n")
        return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Post-run pipeline audit")
    parser.add_argument("--cycle-id", help="Specific cycle_id to audit (default: most recent)")
    args = parser.parse_args()

    success = run_audit(args.cycle_id)
    sys.exit(0 if success else 1)
