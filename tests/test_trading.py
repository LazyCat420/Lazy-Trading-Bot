"""Tests for Phase 3 Trading Engine.

Covers: models, signal router, paper trader, price monitor.
"""

from __future__ import annotations

import math
import os
import tempfile
import uuid
from datetime import date, timedelta
from unittest.mock import patch

import pytest

# ── Setup: point DB to a temp file so tests don't clobber production ──
_test_db_dir = tempfile.mkdtemp()
os.environ.setdefault("LAZY_DATA_DIR", _test_db_dir)

from app.models.trading import Order, Position, PortfolioSnapshot, PriceTrigger  # noqa: E402


# ══════════════════════════════════════════════════════════════════════
# 1.  Model validation
# ══════════════════════════════════════════════════════════════════════


class TestModels:
    """Pydantic model instantiation tests."""

    def test_position_create(self):
        p = Position(ticker="AAPL", qty=10, avg_entry_price=150.0)
        assert p.ticker == "AAPL"
        assert p.qty == 10
        assert p.avg_entry_price == 150.0
        assert p.unrealized_pnl == 0.0

    def test_order_create(self):
        o = Order(
            id=str(uuid.uuid4()),
            ticker="GOOG",
            side="buy",
            qty=5,
            price=100.0,
        )
        assert o.side == "buy"
        assert o.status == "filled"
        assert o.order_type == "market"

    def test_portfolio_snapshot(self):
        s = PortfolioSnapshot(
            cash_balance=10000.0,
            total_positions_value=5000.0,
            total_portfolio_value=15000.0,
        )
        assert s.total_portfolio_value == 15000.0
        assert s.realized_pnl == 0.0

    def test_price_trigger(self):
        t = PriceTrigger(
            id=str(uuid.uuid4()),
            ticker="TSLA",
            trigger_type="stop_loss",
            trigger_price=200.0,
            qty=10,
        )
        assert t.trigger_type == "stop_loss"
        assert t.status == "active"
        assert t.action == "sell"


# ══════════════════════════════════════════════════════════════════════
# 2.  Signal Router
# ══════════════════════════════════════════════════════════════════════


class TestSignalRouter:
    """Test conviction → order conversion and safety guards."""

    @pytest.fixture(autouse=True)
    def setup_router(self, tmp_path):
        """Create a router with known risk params."""
        risk_params = {
            "max_position_size_pct": 10.0,
            "max_portfolio_allocation_pct": 30.0,
            "max_orders_per_day": 5,
            "daily_loss_limit_pct": 5.0,
            "cooldown_days": 7,
            "account_size_usd": 10000,
        }
        # Patch settings to use tmp_path for config
        risk_file = tmp_path / "user_config" / "risk_params.json"
        risk_file.parent.mkdir(parents=True, exist_ok=True)
        import json
        risk_file.write_text(json.dumps(risk_params))

        with patch("app.engine.signal_router.settings") as mock_settings:
            mock_settings.USER_CONFIG_DIR = risk_file.parent
            from app.engine.signal_router import SignalRouter
            self.router = SignalRouter()
            yield  # keep mock active during test execution

    def test_buy_signal_high_conviction(self):
        """Conviction >= 0.7 should produce a BUY."""
        result = self.router.evaluate(
            ticker="AAPL",
            conviction_score=0.85,
            current_price=150.0,
            cash_balance=10000.0,
            total_portfolio_value=10000.0,
        )
        assert result is not None
        assert result["side"] == "buy"
        assert result["signal"] == "BUY"
        assert result["qty"] > 0

    def test_sell_signal_low_conviction(self):
        """Conviction <= 0.3 with existing position should produce SELL."""
        result = self.router.evaluate(
            ticker="AAPL",
            conviction_score=0.2,
            current_price=150.0,
            cash_balance=5000.0,
            total_portfolio_value=10000.0,
            existing_position_qty=10,
        )
        assert result is not None
        assert result["side"] == "sell"
        assert result["signal"] == "SELL"
        assert result["qty"] == 10  # full position

    def test_hold_signal_mid_conviction(self):
        """Conviction between 0.3 and 0.7 should return None (HOLD)."""
        result = self.router.evaluate(
            ticker="AAPL",
            conviction_score=0.5,
            current_price=150.0,
            cash_balance=10000.0,
            total_portfolio_value=10000.0,
        )
        assert result is None

    def test_max_orders_per_day_guard(self):
        """Should skip when daily order limit reached."""
        result = self.router.evaluate(
            ticker="AAPL",
            conviction_score=0.9,
            current_price=150.0,
            cash_balance=10000.0,
            total_portfolio_value=10000.0,
            orders_today=5,  # at limit
        )
        assert result is None

    def test_daily_loss_limit_guard(self):
        """Should skip when daily loss exceeds limit."""
        result = self.router.evaluate(
            ticker="AAPL",
            conviction_score=0.9,
            current_price=150.0,
            cash_balance=10000.0,
            total_portfolio_value=10000.0,
            daily_pnl_pct=-6.0,  # exceeds 5% limit
        )
        assert result is None

    def test_cooldown_guard(self):
        """Should skip buy if ticker was recently sold."""
        result = self.router.evaluate(
            ticker="AAPL",
            conviction_score=0.9,
            current_price=150.0,
            cash_balance=10000.0,
            total_portfolio_value=10000.0,
            last_sold_date=date.today() - timedelta(days=2),  # within 7-day cooldown
        )
        assert result is None

    def test_no_duplicate_buy(self):
        """Should skip buy if already holding position."""
        result = self.router.evaluate(
            ticker="AAPL",
            conviction_score=0.9,
            current_price=150.0,
            cash_balance=10000.0,
            total_portfolio_value=10000.0,
            existing_position_qty=5,
        )
        assert result is None

    def test_position_sizing_respects_limits(self):
        """Position size should not exceed max_position_size_pct."""
        result = self.router.evaluate(
            ticker="AAPL",
            conviction_score=0.9,
            current_price=150.0,
            cash_balance=10000.0,
            total_portfolio_value=10000.0,
        )
        assert result is not None
        # max_position_size_pct = 10% of $10,000 = $1,000 → 6 shares @ $150
        expected_max_qty = math.floor(1000 / 150)
        assert result["qty"] == expected_max_qty

    def test_sell_no_position_returns_none(self):
        """Sell signal with no position should skip."""
        result = self.router.evaluate(
            ticker="AAPL",
            conviction_score=0.1,
            current_price=150.0,
            cash_balance=10000.0,
            total_portfolio_value=10000.0,
            existing_position_qty=0,
        )
        assert result is None


# ══════════════════════════════════════════════════════════════════════
# 3.  Paper Trader (needs DuckDB)
# ══════════════════════════════════════════════════════════════════════


class TestPaperTrader:
    """Test buy/sell execution, position tracking, and cash management."""

    @pytest.fixture(autouse=True)
    def setup_trader(self, tmp_path):
        """Create a fresh DuckDB and PaperTrader for each test."""
        import app.database as db_mod

        # Close any existing connection
        if db_mod._connection is not None:
            try:
                db_mod._connection.close()
            except Exception:
                pass
            db_mod._connection = None

        db_path = tmp_path / "test_trading.duckdb"
        with patch.object(db_mod.settings, "DB_PATH", db_path):
            # Force re-init
            conn = db_mod.get_db()

            from app.services.paper_trader import PaperTrader
            self.trader = PaperTrader(starting_balance=10000.0)

            yield

            # Cleanup
            try:
                conn.close()
            except Exception:
                pass
            db_mod._connection = None

    def test_initial_balance(self):
        """Starting balance should be $10,000."""
        cash = self.trader.get_cash_balance()
        assert cash == 10000.0

    def test_buy_reduces_cash(self):
        """Buy order should reduce cash by cost."""
        order = self.trader.buy("AAPL", qty=5, price=100.0)
        assert order is not None
        assert order.side == "buy"
        assert order.qty == 5
        # Cash should be 10000 - 500 = 9500
        cash = self.trader.get_cash_balance()
        assert cash == 9500.0

    def test_buy_creates_position(self):
        """Buy should create a new position."""
        self.trader.buy("AAPL", qty=5, price=100.0)
        positions = self.trader.get_positions()
        assert len(positions) == 1
        assert positions[0]["ticker"] == "AAPL"
        assert positions[0]["qty"] == 5

    def test_sell_closes_position(self):
        """Sell should remove the position and add cash."""
        self.trader.buy("AAPL", qty=5, price=100.0)
        order = self.trader.sell("AAPL", qty=5, price=120.0)
        assert order is not None
        assert order.side == "sell"

        # Position should be gone
        positions = self.trader.get_positions()
        assert len(positions) == 0

        # Cash: started 10000, bought 500, sold 600 = 10100
        cash = self.trader.get_cash_balance()
        assert cash == 10100.0

    def test_insufficient_balance_rejected(self):
        """Buy exceeding cash should be rejected."""
        order = self.trader.buy("AAPL", qty=200, price=100.0)
        assert order is None

    def test_dca_buy(self):
        """Second buy should dollar-cost average."""
        self.trader.buy("AAPL", qty=5, price=100.0)
        self.trader.buy("AAPL", qty=5, price=120.0)
        positions = self.trader.get_positions()
        assert len(positions) == 1
        assert positions[0]["qty"] == 10
        # Avg: (5*100 + 5*120) / 10 = 110
        assert positions[0]["avg_entry_price"] == 110.0

    def test_sell_no_position_rejected(self):
        """Sell without position should be rejected."""
        order = self.trader.sell("AAPL", qty=5, price=100.0)
        assert order is None

    def test_order_history(self):
        """Orders should be recorded."""
        self.trader.buy("AAPL", qty=5, price=100.0)
        self.trader.sell("AAPL", qty=5, price=120.0)
        orders = self.trader.get_orders()
        assert len(orders) == 2
        assert orders[0]["side"] == "sell"  # most recent first
        assert orders[1]["side"] == "buy"

    def test_portfolio_summary(self):
        """Portfolio should reflect cash + positions."""
        self.trader.buy("AAPL", qty=5, price=100.0)
        portfolio = self.trader.get_portfolio()
        assert portfolio["cash_balance"] == 9500.0
        assert portfolio["positions_count"] == 1
        assert portfolio["total_positions_value"] == 500.0
        assert portfolio["total_portfolio_value"] == 10000.0

    def test_triggers_created(self):
        """Setting triggers should create DB records."""
        self.trader.buy("AAPL", qty=10, price=100.0)
        triggers = self.trader.set_triggers_for_position(
            ticker="AAPL",
            entry_price=100.0,
            qty=10,
            stop_loss_pct=5.0,
            take_profit_pct=15.0,
        )
        assert len(triggers) == 2  # SL + TP

        active_triggers = self.trader.get_triggers()
        assert len(active_triggers) == 2
        types = {t["trigger_type"] for t in active_triggers}
        assert "stop_loss" in types
        assert "take_profit" in types


# ══════════════════════════════════════════════════════════════════════
# 4. Smoke test — imports work
# ══════════════════════════════════════════════════════════════════════


class TestImports:
    """Verify all Phase 3 modules import cleanly."""

    def test_import_models(self):
        from app.models.trading import Order, Position
        assert Position is not None
        assert Order is not None

    def test_import_signal_router(self):
        from app.engine.signal_router import SignalRouter
        assert SignalRouter is not None

    def test_import_paper_trader(self):
        from app.services.paper_trader import PaperTrader
        assert PaperTrader is not None

    def test_import_price_monitor(self):
        from app.services.price_monitor import PriceMonitor
        assert PriceMonitor is not None
