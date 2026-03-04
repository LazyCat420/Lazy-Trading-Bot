"""Post-run Audit Tools Checklist Script."""
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import duckdb

print("=" * 60)
print("AUDIT TOOLS VERIFICATION")
print("=" * 60)

db = duckdb.connect("d:/Github/Lazy-Trading-Bot/data/trading_bot.duckdb", read_only=True)

# ── Part 1: Infrastructure & Data Integrity ──

print("\n[1] The Database Write Check (llm_audit_logs):")
try:
    total_calls = db.execute("SELECT count(*) FROM llm_audit_logs").fetchone()[0]
    trades = db.execute("SELECT count(*) FROM trade_decisions").fetchone()[0]
    print(f"  Total LLM calls logged: {total_calls}")
    print(f"  Total Trade Decisions: {trades}")
    print(f"  Difference: {total_calls - trades}")

    # Check JSON parsing failures
    json_failures = db.execute("""
        SELECT count(*) FROM llm_audit_logs 
        WHERE (parsed_json IS NULL OR parsed_json = '{}') 
          AND agent_step = 'trading_decision'
    """).fetchone()[0]
    print(f"  Calls with empty/invalid parsed_json: {json_failures}")
except Exception as e:
    print(f"  Error: {e}")

print("\n[2] The Context Injection Check:")
try:
    context_check = db.execute("""
        SELECT ticker, length(user_context) as ctx_len 
        FROM llm_audit_logs 
        WHERE agent_step = 'trading_decision' 
        ORDER BY created_at DESC LIMIT 5
    """).fetchall()
    print("  Recent user_context lengths:")
    for c in context_check:
        print(f"    {c[0]}: {c[1]} characters (approx {c[1]//4} tokens)")
except Exception as e:
    print(f"  Error: {e}")

print("\n[4] The Rejection/Quarantine Table Check:")
try:
    rejected = db.execute("SELECT count(*) FROM rejected_symbols").fetchone()[0]
    print(f"  Total rejected symbols: {rejected}")
    if rejected > 0:
        sample = db.execute("SELECT symbol, reason FROM rejected_symbols LIMIT 5").fetchall()
        for s in sample:
            print(f"    {s[0]}: {s[1]}")
except Exception as e:
    print(f"  Error: {e}")

# ── Part 2: AI Logic & Hallucination Audit ──

print("\n[5/6/7] LLM Reasoning & Math Sample (BUY decisions):")
try:
    buys = db.execute("""
        SELECT symbol, rationale, risk_level, time_horizon, ts, confidence
        FROM trade_decisions
        WHERE action = 'BUY'
        ORDER BY ts DESC LIMIT 3
    """).fetchall()
    
    if buys:
        for i, b in enumerate(buys):
            print(f"\n  BUY Decision {i+1} [{b[0]}] (conf: {b[5]} %, risk: {b[2]}, hz: {b[3]}):")
            print(f"    Rationale: {b[1][:100]}...")
    else:
        print("  No BUY decisions found.")
except Exception as e:
    print(f"  Error: {e}")

print("\n[8] Execution Reconciliation Check:")
try:
    # Do we have multiple executions per decision?
    execs = db.execute("""
        SELECT decision_id, count(*) 
        FROM trade_executions 
        GROUP BY decision_id HAVING count(*) > 1
    """).fetchall()
    print(f"  Decisions with multiple executions (loops): {len(execs)}")
    
    # Do we have failed executions?
    failed = db.execute("SELECT count(*), broker_error FROM trade_executions WHERE status != 'filled' AND ts >= current_date GROUP BY broker_error").fetchall()
    if failed:
        print(f"  Failed executions:")
        for f in failed:
            print(f"    {f[0]} failures: {f[1]}")
    else:
        print("  No failed executions.")
except Exception as e:
    print(f"  Error: {e}")

db.close()
print("\n" + "=" * 60)
print("AUDIT COMPLETE")
print("=" * 60)
