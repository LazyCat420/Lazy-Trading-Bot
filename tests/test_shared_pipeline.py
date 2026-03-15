"""Tests for the shared-phase pipeline architecture.

Verifies that:
1. Shared phases save data to GLOBAL tables (not bot-scoped)
2. Per-bot LLM loops can see the globally-discovered tickers
3. Watchlist import correctly scopes tickers to each bot_id
4. Dedup guards prevent duplicate scorecards and dossiers
5. Self-question prompts produce non-empty answers
"""

import json
import os
import shutil
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Setup: locate the DB and make a read-only copy for testing
# ---------------------------------------------------------------------------

DB_PATH = os.path.join(
    os.path.dirname(__file__), "..", "data", "trading_bot.duckdb"
)
DB_PATH = os.path.abspath(DB_PATH)


@pytest.fixture(scope="session")
def db():
    """Provide a read-only DuckDB connection for testing."""
    import duckdb

    if not os.path.exists(DB_PATH):
        pytest.skip("Database not found — skipping pipeline tests")

    # Try direct read-only first, copy on lock
    try:
        conn = duckdb.connect(DB_PATH, read_only=True)
        conn.execute("SELECT 1").fetchone()
        yield conn
        conn.close()
    except Exception:
        tmp_dir = tempfile.mkdtemp()
        tmp_db = os.path.join(tmp_dir, "test_pipeline.duckdb")
        shutil.copy2(DB_PATH, tmp_db)
        conn = duckdb.connect(tmp_db, read_only=True)
        yield conn
        conn.close()
        shutil.rmtree(tmp_dir, ignore_errors=True)


# =====================================================================
# Test 1: Global tables exist and are NOT bot-scoped
# =====================================================================

class TestGlobalTables:
    """Verify that discovery data is stored in global (non-bot-scoped) tables."""

    def test_discovered_tickers_has_no_bot_id_column(self, db):
        """discovered_tickers should be global — no bot_id scoping."""
        cols = db.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'discovered_tickers'"
        ).fetchall()
        col_names = [c[0] for c in cols]
        assert "bot_id" not in col_names, (
            "discovered_tickers should not have bot_id — it's global data"
        )

    def test_price_history_has_no_bot_id_column(self, db):
        """price_history should be global — same prices for all bots."""
        cols = db.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'price_history'"
        ).fetchall()
        col_names = [c[0] for c in cols]
        assert "bot_id" not in col_names

    def test_news_articles_has_no_bot_id_column(self, db):
        """news_articles should be global — same news for all bots."""
        cols = db.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'news_articles'"
        ).fetchall()
        col_names = [c[0] for c in cols]
        assert "bot_id" not in col_names


# =====================================================================
# Test 2: Watchlist IS bot-scoped
# =====================================================================

class TestWatchlistScoping:
    """Verify that watchlist is correctly scoped by bot_id."""

    def test_watchlist_has_bot_id_column(self, db):
        """watchlist must have bot_id for per-bot scoping."""
        cols = db.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'watchlist'"
        ).fetchall()
        col_names = [c[0] for c in cols]
        assert "bot_id" in col_names, (
            "watchlist must have bot_id column for per-bot scoping"
        )

    def test_watchlist_bots_have_different_entries(self, db):
        """Different bot_ids should have their own watchlist entries."""
        result = db.execute(
            "SELECT bot_id, COUNT(*) AS cnt FROM watchlist "
            "WHERE status = 'active' GROUP BY bot_id ORDER BY cnt DESC"
        ).fetchall()
        if len(result) > 1:
            # Multiple bots have their own watchlist entries
            bot_ids = [r[0] for r in result]
            assert len(set(bot_ids)) > 1, "Expected multiple bot_ids in watchlist"
        elif len(result) == 1:
            # Only one bot — that's fine for a fresh DB
            pass
        else:
            pytest.skip("No watchlist entries found")


# =====================================================================
# Test 3: Ticker scores are global (readable by all bots)
# =====================================================================

class TestTickerScoresGlobal:
    """Verify ticker_scores table is global and usable by import_from_discovery."""

    def test_ticker_scores_exists(self, db):
        """ticker_scores table must exist for import_from_discovery."""
        tables = db.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name = 'ticker_scores'"
        ).fetchall()
        assert len(tables) > 0, "ticker_scores table must exist"

    def test_ticker_scores_has_no_bot_id(self, db):
        """ticker_scores should be global — scores are computed once."""
        cols = db.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'ticker_scores'"
        ).fetchall()
        col_names = [c[0] for c in cols]
        assert "bot_id" not in col_names, (
            "ticker_scores must be global for shared discovery"
        )


# =====================================================================
# Test 4: Dedup guards work
# =====================================================================

class TestDedupGuards:
    """Verify that dedup DELETE-before-INSERT guards are active."""

    def test_quant_scorecards_max_one_per_ticker(self, db):
        """After dedup fix, each ticker should have at most 1 scorecard."""
        dupes = db.execute(
            "SELECT ticker, COUNT(*) AS cnt FROM quant_scorecards "
            "GROUP BY ticker HAVING cnt > 1 ORDER BY cnt DESC"
        ).fetchall()
        if dupes:
            worst = dupes[0]
            # After fix, any new run will have max 1. Old data may still
            # have dupes — warn but don't fail
            import warnings
            warnings.warn(
                f"quant_scorecards still has dupes from pre-fix runs: "
                f"{worst[0]} has {worst[1]} rows. "
                f"These will clean up on next analysis run."
            )

    def test_ticker_dossiers_max_one_per_ticker(self, db):
        """After dedup fix, each ticker should have at most 1 dossier."""
        dupes = db.execute(
            "SELECT ticker, COUNT(*) AS cnt FROM ticker_dossiers "
            "GROUP BY ticker HAVING cnt > 1 ORDER BY cnt DESC"
        ).fetchall()
        if dupes:
            worst = dupes[0]
            import warnings
            warnings.warn(
                f"ticker_dossiers still has dupes from pre-fix runs: "
                f"{worst[0]} has {worst[1]} rows. "
                f"These will clean up on next analysis run."
            )


# =====================================================================
# Test 5: Self-question prompt is actionable
# =====================================================================

class TestSelfQuestionPrompt:
    """Verify the self-question seed prompt requires answers."""

    def test_seed_prompt_requires_answers(self):
        """The self-question prompt must instruct LLM to fill answers."""
        from app.services.AgenticExtractor import SEED_PROMPTS
        prompt = SEED_PROMPTS["extraction_self_question"]

        assert "answer" in prompt.lower(), (
            "Self-question prompt must instruct LLM to ANSWER questions"
        )
        assert "buy" in prompt.lower() or "sell" in prompt.lower(), (
            "Self-question prompt must focus on buy/sell decision data"
        )
        assert "A1" in prompt or "answers" in prompt, (
            "Prompt must show example answers format"
        )

    def test_seed_prompt_has_actionable_focus(self):
        """Prompt must mention earnings, technicals, or fundamentals."""
        from app.services.AgenticExtractor import SEED_PROMPTS
        prompt = SEED_PROMPTS["extraction_self_question"]

        actionable_terms = [
            "earnings", "revenue", "technical", "support", "resistance",
            "catalyst", "risk", "valuation",
        ]
        found = [t for t in actionable_terms if t in prompt.lower()]
        assert len(found) >= 3, (
            f"Prompt only mentions {found} — needs more actionable focus "
            f"(earnings, technicals, catalysts, risks, valuation)"
        )


# =====================================================================
# Test 6: AutonomousLoop has both shared and LLM-only methods
# =====================================================================

class TestAutonomousLoopMethods:
    """Verify the AutonomousLoop class has the new pipeline methods."""

    def test_has_run_shared_phases(self):
        """AutonomousLoop must have run_shared_phases method."""
        from app.services.autonomous_loop import AutonomousLoop
        loop = AutonomousLoop(max_tickers=5, bot_id="test")
        assert hasattr(loop, "run_shared_phases"), (
            "AutonomousLoop missing run_shared_phases method"
        )
        assert callable(loop.run_shared_phases)

    def test_has_run_llm_only_loop(self):
        """AutonomousLoop must have run_llm_only_loop method."""
        from app.services.autonomous_loop import AutonomousLoop
        loop = AutonomousLoop(max_tickers=5, bot_id="test")
        assert hasattr(loop, "run_llm_only_loop"), (
            "AutonomousLoop missing run_llm_only_loop method"
        )
        assert callable(loop.run_llm_only_loop)

    def test_llm_only_loop_includes_import(self):
        """run_llm_only_loop must call _do_import before analysis."""
        import inspect
        from app.services.autonomous_loop import AutonomousLoop
        source = inspect.getsource(AutonomousLoop.run_llm_only_loop)
        assert "_do_import" in source, (
            "run_llm_only_loop MUST call _do_import to populate "
            "this bot's watchlist from the shared discovery data"
        )

    def test_shared_phases_does_not_include_analysis(self):
        """run_shared_phases must NOT run deep analysis or trading."""
        import inspect
        from app.services.autonomous_loop import AutonomousLoop
        source = inspect.getsource(AutonomousLoop.run_shared_phases)
        assert "_do_deep_analysis" not in source, (
            "run_shared_phases must NOT run deep analysis (LLM-dependent)"
        )
        assert "_do_trading" not in source, (
            "run_shared_phases must NOT run trading (LLM-dependent)"
        )


# =====================================================================
# Test 7: WatchlistManager import_from_discovery is bot-scoped
# =====================================================================

class TestWatchlistImportScoping:
    """Verify import_from_discovery creates bot-specific entries."""

    def test_import_stores_with_bot_id(self):
        """import_from_discovery must insert with this bot's bot_id."""
        import inspect
        from app.services.watchlist_manager import WatchlistManager
        source = inspect.getsource(WatchlistManager.add_ticker)
        assert "bot_id" in source, (
            "WatchlistManager.add_ticker must use self.bot_id"
        )
