"""Tests for Multi-Bot Leaderboard system.

Tests bot registry CRUD, bot-scoped PaperTrader isolation,
and leaderboard ranking.
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

    def test_leaderboard_ranking(self) -> None:
        """Leaderboard should rank by total_pnl descending."""
        bot_a = BotRegistry.register_bot("model-a")
        bot_b = BotRegistry.register_bot("model-b")
        db = get_db()
        db.execute(
            "UPDATE bots SET total_pnl = 500.0 WHERE bot_id = ?",
            [bot_a["bot_id"]],
        )
        db.execute(
            "UPDATE bots SET total_pnl = 1200.0 WHERE bot_id = ?",
            [bot_b["bot_id"]],
        )
        db.commit()
        lb = BotRegistry.get_leaderboard()
        assert len(lb) == 2
        assert lb[0]["bot_id"] == bot_b["bot_id"]  # Higher PnL first
        assert lb[0]["rank"] == 1
        assert lb[1]["rank"] == 2


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
        trader_b = PaperTrader(starting_balance=20000, bot_id="bot-cash-b")

        trader_a.buy("TSM", 1, 100.0)

        # A's cash should decrease, B's should remain unchanged
        assert trader_a.get_cash_balance() == pytest.approx(9900, abs=1)
        assert trader_b.get_cash_balance() == pytest.approx(20000, abs=1)

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
