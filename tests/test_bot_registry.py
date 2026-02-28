"""Tests for Multi-Bot Leaderboard system.

Tests bot registry CRUD, bot-scoped PaperTrader isolation,
queue ordering, hard delete, and leaderboard ranking.
"""

from __future__ import annotations

import pytest

from app.database import get_db
from app.services.bot_registry import BotRegistry
from app.services.paper_trader import PaperTrader


@pytest.fixture(autouse=True)
def _clean_bots():
    """Clean up bot data before each test."""
    db = get_db()
    # Safely delete — tables might have bot_id column from migration
    for table in ["positions", "orders", "portfolio_snapshots",
                   "price_triggers", "watchlist", "pipeline_events"]:
        try:
            db.execute(f"DELETE FROM {table} WHERE bot_id != 'default'")  # noqa: S608
        except Exception:
            pass
    try:
        db.execute("DELETE FROM bots")
    except Exception:
        pass
    db.commit()
    yield


class TestBotRegistry:
    """Test CRUD operations on the bots table."""

    def test_register_bot(self) -> None:
        """Should create a bot and return its data."""
        bot = BotRegistry.register_bot(
            model_name="qwen3-4b",
            display_name="Qwen3 Trader",
            context_length=16384,
            temperature=0.5,
        )
        assert bot is not None
        assert bot["model_name"] == "qwen3-4b"
        assert bot["display_name"] == "Qwen3 Trader"
        assert bot["context_length"] == 16384
        assert bot["temperature"] == 0.5
        assert bot["status"] == "active"
        assert len(bot["bot_id"]) == 12

    def test_list_bots(self) -> None:
        """Should list active bots."""
        BotRegistry.register_bot("model-a", "Bot A")
        BotRegistry.register_bot("model-b", "Bot B")
        bots = BotRegistry.list_bots()
        assert len(bots) == 2

    def test_list_bots_ordered_by_queue_order(self) -> None:
        """list_bots should return bots in queue_order ASC."""
        bot_a = BotRegistry.register_bot("model-a", "Bot A")
        bot_b = BotRegistry.register_bot("model-b", "Bot B")
        bot_c = BotRegistry.register_bot("model-c", "Bot C")

        # Set custom queue order: C=0, A=1, B=2
        BotRegistry.reorder_bots([
            bot_c["bot_id"], bot_a["bot_id"], bot_b["bot_id"],
        ])

        bots = BotRegistry.list_bots()
        assert len(bots) == 3
        assert bots[0]["bot_id"] == bot_c["bot_id"]
        assert bots[1]["bot_id"] == bot_a["bot_id"]
        assert bots[2]["bot_id"] == bot_b["bot_id"]

    def test_get_bot(self) -> None:
        """Should retrieve a bot by ID."""
        created = BotRegistry.register_bot("model-x")
        fetched = BotRegistry.get_bot(created["bot_id"])
        assert fetched is not None
        assert fetched["model_name"] == "model-x"

    def test_get_nonexistent_bot(self) -> None:
        """Should return None for missing bot."""
        assert BotRegistry.get_bot("nonexistent") is None

    def test_update_settings(self) -> None:
        """Should update allowed fields."""
        bot = BotRegistry.register_bot("model-y", temperature=0.3)
        updated = BotRegistry.update_bot_settings(
            bot["bot_id"],
            {"temperature": 0.7, "context_length": 32768},
        )
        assert updated is not None
        assert updated["temperature"] == 0.7
        assert updated["context_length"] == 32768

    def test_deactivate_bot(self) -> None:
        """Should soft-delete by setting status='inactive'."""
        bot = BotRegistry.register_bot("model-z")
        BotRegistry.deactivate_bot(bot["bot_id"])
        # Should not appear in active list
        active = BotRegistry.list_bots()
        assert all(b["bot_id"] != bot["bot_id"] for b in active)
        # Should appear in full list
        all_bots = BotRegistry.list_bots(include_inactive=True)
        found = [b for b in all_bots if b["bot_id"] == bot["bot_id"]]
        assert len(found) == 1
        assert found[0]["status"] == "inactive"

    def test_leaderboard_empty(self) -> None:
        """Leaderboard should return empty list with no bots."""
        lb = BotRegistry.get_leaderboard()
        assert lb == []


class TestBotReorder:
    """Test bot queue ordering for Run All."""

    def test_reorder_bots(self) -> None:
        """Should set queue_order correctly."""
        bot_a = BotRegistry.register_bot("model-a", "Bot A")
        bot_b = BotRegistry.register_bot("model-b", "Bot B")
        bot_c = BotRegistry.register_bot("model-c", "Bot C")

        # Reverse the order
        BotRegistry.reorder_bots([
            bot_c["bot_id"], bot_b["bot_id"], bot_a["bot_id"],
        ])

        # Verify queue_order is persisted
        db = get_db()
        rows = db.execute(
            "SELECT bot_id, queue_order FROM bots ORDER BY queue_order ASC",
        ).fetchall()
        assert rows[0] == (bot_c["bot_id"], 0)
        assert rows[1] == (bot_b["bot_id"], 1)
        assert rows[2] == (bot_a["bot_id"], 2)

    def test_reorder_reflects_in_leaderboard(self) -> None:
        """Leaderboard should follow queue_order, not PnL."""
        bot_a = BotRegistry.register_bot("model-a", "Bot A")
        bot_b = BotRegistry.register_bot("model-b", "Bot B")

        db = get_db()
        # Give bot_a higher PnL — leaderboard should NOT sort by this
        db.execute(
            "UPDATE bots SET total_pnl = 999.0 WHERE bot_id = ?",
            [bot_a["bot_id"]],
        )
        db.execute(
            "UPDATE bots SET total_pnl = 1.0 WHERE bot_id = ?",
            [bot_b["bot_id"]],
        )
        db.commit()

        # Set queue order: B first, then A
        BotRegistry.reorder_bots([bot_b["bot_id"], bot_a["bot_id"]])

        lb = BotRegistry.get_leaderboard()
        assert len(lb) == 2
        # B should be first despite lower PnL (queue order wins)
        assert lb[0]["bot_id"] == bot_b["bot_id"]
        assert lb[1]["bot_id"] == bot_a["bot_id"]

    def test_reorder_reflects_in_list_bots(self) -> None:
        """list_bots (used by run_all) should follow queue_order."""
        bot_a = BotRegistry.register_bot("model-a", "Bot A")
        bot_b = BotRegistry.register_bot("model-b", "Bot B")

        BotRegistry.reorder_bots([bot_b["bot_id"], bot_a["bot_id"]])

        bots = BotRegistry.list_bots()
        assert bots[0]["bot_id"] == bot_b["bot_id"]
        assert bots[1]["bot_id"] == bot_a["bot_id"]

    def test_swap_adjacent_bots(self) -> None:
        """Simulates the frontend's up/down arrow swap."""
        bot_a = BotRegistry.register_bot("model-a", "Bot A")
        bot_b = BotRegistry.register_bot("model-b", "Bot B")
        bot_c = BotRegistry.register_bot("model-c", "Bot C")

        # Initial order
        BotRegistry.reorder_bots([
            bot_a["bot_id"], bot_b["bot_id"], bot_c["bot_id"],
        ])

        # "Move B up" → swap positions 0 and 1
        ids = [bot_a["bot_id"], bot_b["bot_id"], bot_c["bot_id"]]
        ids[0], ids[1] = ids[1], ids[0]
        BotRegistry.reorder_bots(ids)

        bots = BotRegistry.list_bots()
        assert bots[0]["bot_id"] == bot_b["bot_id"]
        assert bots[1]["bot_id"] == bot_a["bot_id"]
        assert bots[2]["bot_id"] == bot_c["bot_id"]

    def test_leaderboard_rank_number_follows_display_order(self) -> None:
        """The rank field should follow display order (1, 2, 3...)."""
        bot_a = BotRegistry.register_bot("model-a", "Bot A")
        bot_b = BotRegistry.register_bot("model-b", "Bot B")

        BotRegistry.reorder_bots([bot_b["bot_id"], bot_a["bot_id"]])

        lb = BotRegistry.get_leaderboard()
        assert lb[0]["rank"] == 1
        assert lb[0]["bot_id"] == bot_b["bot_id"]
        assert lb[1]["rank"] == 2
        assert lb[1]["bot_id"] == bot_a["bot_id"]

    def test_default_queue_order_is_zero(self) -> None:
        """New bots should start with queue_order=0."""
        bot = BotRegistry.register_bot("model-test")
        db = get_db()
        row = db.execute(
            "SELECT queue_order FROM bots WHERE bot_id = ?",
            [bot["bot_id"]],
        ).fetchone()
        assert row[0] == 0


class TestBotHardDelete:
    """Test hard deletion of bots and associated data."""

    def test_delete_removes_bot(self) -> None:
        """delete_bot should remove the bot from the table."""
        bot = BotRegistry.register_bot("model-del")
        BotRegistry.delete_bot(bot["bot_id"])
        assert BotRegistry.get_bot(bot["bot_id"]) is None

    def test_delete_not_in_leaderboard(self) -> None:
        """Deleted bot should not appear in leaderboard."""
        bot_a = BotRegistry.register_bot("model-a")
        bot_b = BotRegistry.register_bot("model-b")
        BotRegistry.delete_bot(bot_a["bot_id"])
        lb = BotRegistry.get_leaderboard()
        assert len(lb) == 1
        assert lb[0]["bot_id"] == bot_b["bot_id"]

    def test_delete_cleans_up_positions(self) -> None:
        """delete_bot should remove positions for that bot."""
        bot = BotRegistry.register_bot("model-cleanup")
        trader = PaperTrader(starting_balance=10000, bot_id=bot["bot_id"])
        trader.buy("AAPL", 2, 150.0)
        assert len(trader.get_positions()) == 1

        BotRegistry.delete_bot(bot["bot_id"])

        # Positions should be gone
        db = get_db()
        remaining = db.execute(
            "SELECT COUNT(*) FROM positions WHERE bot_id = ?",
            [bot["bot_id"]],
        ).fetchone()[0]
        assert remaining == 0

    def test_delete_cleans_up_orders(self) -> None:
        """delete_bot should remove orders for that bot."""
        bot = BotRegistry.register_bot("model-ord")
        trader = PaperTrader(starting_balance=10000, bot_id=bot["bot_id"])
        trader.buy("TSLA", 1, 200.0)
        assert len(trader.get_orders()) == 1

        BotRegistry.delete_bot(bot["bot_id"])

        db = get_db()
        remaining = db.execute(
            "SELECT COUNT(*) FROM orders WHERE bot_id = ?",
            [bot["bot_id"]],
        ).fetchone()[0]
        assert remaining == 0

    def test_delete_does_not_affect_other_bots(self) -> None:
        """Deleting one bot should not affect another bot's data."""
        bot_a = BotRegistry.register_bot("model-keep-a")
        bot_b = BotRegistry.register_bot("model-keep-b")

        trader_a = PaperTrader(starting_balance=10000, bot_id=bot_a["bot_id"])
        trader_b = PaperTrader(starting_balance=10000, bot_id=bot_b["bot_id"])
        trader_a.buy("AAPL", 2, 150.0)
        trader_b.buy("NVDA", 1, 800.0)

        # Delete bot A
        BotRegistry.delete_bot(bot_a["bot_id"])

        # Bot B should still have its data
        assert len(trader_b.get_positions()) == 1
        assert trader_b.get_positions()[0]["ticker"] == "NVDA"
        assert len(trader_b.get_orders()) == 1

    def test_delete_vs_deactivate(self) -> None:
        """Hard delete should fully remove; deactivate should keep data."""
        bot_soft = BotRegistry.register_bot("model-soft")
        bot_hard = BotRegistry.register_bot("model-hard")

        BotRegistry.deactivate_bot(bot_soft["bot_id"])
        BotRegistry.delete_bot(bot_hard["bot_id"])

        # Soft-deleted: still in DB with status=inactive
        found = BotRegistry.get_bot(bot_soft["bot_id"])
        assert found is not None
        assert found["status"] == "inactive"

        # Hard-deleted: gone completely
        assert BotRegistry.get_bot(bot_hard["bot_id"]) is None


class TestLeaderboardData:
    """Test leaderboard data enrichment (portfolio values, positions)."""

    def test_leaderboard_includes_portfolio_value(self) -> None:
        """Leaderboard should include total_portfolio_value."""
        bot = BotRegistry.register_bot("model-pv")
        # Create a portfolio snapshot
        db = get_db()
        db.execute(
            """INSERT INTO portfolio_snapshots
               (cash_balance, total_portfolio_value, bot_id)
               VALUES (?, ?, ?)""",
            [9000.0, 10500.0, bot["bot_id"]],
        )
        db.commit()

        lb = BotRegistry.get_leaderboard()
        assert len(lb) == 1
        assert lb[0]["total_portfolio_value"] == 10500.0

    def test_leaderboard_includes_positions(self) -> None:
        """Leaderboard should include holdings."""
        bot = BotRegistry.register_bot("model-pos")
        trader = PaperTrader(starting_balance=10000, bot_id=bot["bot_id"])
        trader.buy("AAPL", 5, 150.0)

        lb = BotRegistry.get_leaderboard()
        assert len(lb) == 1
        assert lb[0]["positions_count"] == 1
        assert lb[0]["positions"][0]["ticker"] == "AAPL"
        assert lb[0]["positions"][0]["qty"] == 5

    def test_leaderboard_return_pct(self) -> None:
        """return_pct should be calculated from snapshots."""
        bot = BotRegistry.register_bot("model-ret")
        db = get_db()
        # First snapshot = starting balance
        db.execute(
            """INSERT INTO portfolio_snapshots
               (timestamp, cash_balance, total_portfolio_value, bot_id)
               VALUES (TIMESTAMP '2024-01-01', ?, ?, ?)""",
            [10000.0, 10000.0, bot["bot_id"]],
        )
        # Latest snapshot = 10% gain
        db.execute(
            """INSERT INTO portfolio_snapshots
               (timestamp, cash_balance, total_portfolio_value, bot_id)
               VALUES (TIMESTAMP '2024-01-02', ?, ?, ?)""",
            [9000.0, 11000.0, bot["bot_id"]],
        )
        db.commit()

        lb = BotRegistry.get_leaderboard()
        assert len(lb) == 1
        assert lb[0]["return_pct"] == pytest.approx(10.0, abs=0.1)

    def test_leaderboard_includes_queue_order(self) -> None:
        """Should include queue_order in leaderboard response."""
        bot = BotRegistry.register_bot("model-qo")
        BotRegistry.reorder_bots([bot["bot_id"]])

        lb = BotRegistry.get_leaderboard()
        assert len(lb) == 1
        assert "queue_order" in lb[0]
        assert lb[0]["queue_order"] == 0

    def test_leaderboard_includes_provider(self) -> None:
        """Should include provider in leaderboard response."""
        BotRegistry.register_bot("model-prov", provider="ollama")
        lb = BotRegistry.get_leaderboard()
        assert lb[0]["provider"] == "ollama"


class TestBotScopedPaperTrader:
    """Test that PaperTrader isolates portfolios by bot_id."""

    def test_isolated_portfolios(self) -> None:
        """Two bots should have completely separate positions."""
        trader_a = PaperTrader(starting_balance=10000, bot_id="bot-alpha")
        trader_b = PaperTrader(starting_balance=10000, bot_id="bot-beta")

        # Bot A buys AAPL
        trader_a.buy("AAPL", 5, 150.0)
        # Bot B buys NVDA
        trader_b.buy("NVDA", 3, 800.0)

        # Each should only see their own positions
        pos_a = trader_a.get_positions()
        pos_b = trader_b.get_positions()

        assert len(pos_a) == 1
        assert pos_a[0]["ticker"] == "AAPL"
        assert len(pos_b) == 1
        assert pos_b[0]["ticker"] == "NVDA"

    def test_isolated_orders(self) -> None:
        """Order history should be scoped to bot_id."""
        trader_a = PaperTrader(starting_balance=10000, bot_id="bot-ord-a")
        trader_b = PaperTrader(starting_balance=10000, bot_id="bot-ord-b")

        trader_a.buy("MSFT", 2, 400.0)
        trader_b.buy("GOOG", 1, 170.0)

        orders_a = trader_a.get_orders()
        orders_b = trader_b.get_orders()

        assert len(orders_a) == 1
        assert orders_a[0]["ticker"] == "MSFT"
        assert len(orders_b) == 1
        assert orders_b[0]["ticker"] == "GOOG"

    def test_isolated_cash_balance(self) -> None:
        """Cash balance should be tracked independently per bot."""
        trader_a = PaperTrader(starting_balance=10000, bot_id="bot-cash-a")
        trader_b = PaperTrader(starting_balance=10000, bot_id="bot-cash-b")

        trader_a.buy("TSM", 1, 100.0)

        # A's cash should decrease, B's should remain unchanged
        assert trader_a.get_cash_balance() == pytest.approx(9900, abs=1)
        assert trader_b.get_cash_balance() == pytest.approx(10000, abs=1)

    def test_reset_only_affects_own_bot(self) -> None:
        """Resetting one bot's portfolio should not affect another."""
        trader_a = PaperTrader(starting_balance=10000, bot_id="bot-res-a")
        trader_b = PaperTrader(starting_balance=10000, bot_id="bot-res-b")

        trader_a.buy("AAPL", 2, 150.0)
        trader_b.buy("NVDA", 1, 800.0)

        # Reset bot A only
        trader_a.reset_portfolio(new_balance=5000)

        # A should be clean, B should still have its position
        assert len(trader_a.get_positions()) == 0
        assert trader_a.get_cash_balance() == pytest.approx(5000, abs=1)
        assert len(trader_b.get_positions()) == 1
        assert trader_b.get_positions()[0]["ticker"] == "NVDA"

    def test_default_bot_backwards_compatible(self) -> None:
        """PaperTrader() without bot_id should use 'default'."""
        trader = PaperTrader(starting_balance=10000)
        assert trader.bot_id == "default"
        trader.buy("AMD", 1, 100.0)
        orders = trader.get_orders()
        assert len(orders) == 1
