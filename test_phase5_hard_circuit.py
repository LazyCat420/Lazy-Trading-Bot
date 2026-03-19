import os
import sys
import asyncio
import logging
from datetime import datetime, timedelta
from app.database import switch_db, get_db
from app.services.circuit_breaker import CircuitBreaker

# Use audit scratch for these chaotic tests
switch_db("test")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("hard_circuit_test")

def test_circuit_breaker_drawdown():
    logger.info("--- Testing Circuit Breaker (5% Max Drawdown) ---")
    db = get_db()
    
    # 1. Reset state
    CircuitBreaker.reset()
    db.execute("DELETE FROM portfolio_snapshots")
    
    # 2. Insert snapshots simulating a -6% crash over an hour
    now = datetime.now()
    t1 = now - timedelta(minutes=60)
    t2 = now - timedelta(minutes=30)
    t3 = now - timedelta(minutes=5)
    
    # Peak at 100k
    db.execute(
        "INSERT INTO portfolio_snapshots (timestamp, cash_balance, total_portfolio_value) VALUES (?, ?, ?)",
        [t1, 100000, 100000]
    )
    # Down to 96k (-4% -> should be safe)
    db.execute(
        "INSERT INTO portfolio_snapshots (timestamp, cash_balance, total_portfolio_value) VALUES (?, ?, ?)",
        [t2, 96000, 96000]
    )
    tripped, reason = CircuitBreaker.is_tripped()
    logger.info(f"After -4% drawdown, tripped: {tripped}")
    assert not tripped, "Circuit breaker tripped prematurely at 4%!"
    
    # Down to 94k (-6% from peak -> MUST TRIP)
    db.execute(
        "INSERT INTO portfolio_snapshots (timestamp, cash_balance, total_portfolio_value) VALUES (?, ?, ?)",
        [t3, 94000, 94000]
    )
    tripped, reason = CircuitBreaker.is_tripped()
    logger.info(f"After -6% drawdown, tripped: {tripped}. Reason: {reason}")
    assert tripped, "Circuit breaker FAILED to trip at 6% drawdown!"
    
    # 3. Test manual reset requires the portfolio to actually recover (or time to pass)
    # The circuit breaker re-trips INSTANTLY if the snapshots still show a 6% drop
    db.execute("DELETE FROM portfolio_snapshots")
    CircuitBreaker.reset()
    tripped_after, _ = CircuitBreaker.is_tripped()
    assert not tripped_after, "Circuit breaker failed to stay reset after snapshot clearance!"
    logger.info("CircuitBreaker successfully engaged and reset.")

def _crash_subprocess():
    """Run in a child process to simulate a hard Segfault / OOM."""
    switch_db("test")
    db = get_db()
    
    # Start transaction and insert a Canary row
    db.execute("BEGIN TRANSACTION")
    db.execute("DELETE FROM ticker_blacklist WHERE symbol = 'CRASH_CANARY'")
    db.execute("INSERT INTO ticker_blacklist (symbol, reason) VALUES ('CRASH_CANARY', 'Will be rolled back')")
    
    # Hard crash BEFORE commit (bypasses all cleanup, simulates SIGKILL)
    print("CHILD: Hard crashing now...")
    sys.stdout.flush()
    os._exit(1)

def test_hard_crash_recovery():
    logger.info("--- Testing Mid-Cycle DuckDB Crash Recovery (WAL rollback) ---")
    
    # Close DuckDB in parent so the child can open it
    from app.database import reset_connection
    reset_connection()
    
    import multiprocessing
    multiprocessing.set_start_method("spawn", force=True)
    p = multiprocessing.Process(target=_crash_subprocess)
    p.start()
    p.join()  # Wait for it to crash
    
    assert p.exitcode == 1, f"Child process didn't crash as expected (exit code {p.exitcode})"
    
    # Parent process reconnects to DuckDB
    switch_db("test")
    db = get_db()
    
    # Verify WAL rolled back the uncommitted 'CRASH_CANARY' transaction
    row = db.execute("SELECT * FROM ticker_blacklist WHERE symbol = 'CRASH_CANARY'").fetchone()
    logger.info(f"Canary row after crash recovery: {row}")
    
    assert row is None, "DuckDB failed to rollback uncommitted transaction from the crashed process!"
    logger.info("DuckDB WAL successfully maintained ACID consistency after SIGKILL.")

if __name__ == "__main__":
    test_circuit_breaker_drawdown()
    test_hard_crash_recovery()
    logger.info("Phase 5 Chaos Tests Complete!")
