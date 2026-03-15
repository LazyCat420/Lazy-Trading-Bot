"""Battle-tested suite for PriceMonitor."""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from app.services.price_monitor import PriceMonitor

@pytest.fixture
def mock_db():
    with patch("app.services.price_monitor.get_db") as mock_get_db:
        db = MagicMock()
        mock_get_db.return_value = db
        yield db

@pytest.fixture
def mock_trader():
    trader = MagicMock()
    order = MagicMock()
    order.id = "mock-order-id"
    trader.sell.return_value = order
    trader.bot_id = "test_bot"
    return trader

@pytest.mark.asyncio
async def test_check_triggers_no_active(mock_db, mock_trader):
    mock_db.execute.return_value.fetchall.return_value = []
    
    monitor = PriceMonitor(mock_trader)
    result = await monitor.check_triggers()
    
    assert result == []
    mock_trader.sell.assert_not_called()

@pytest.mark.asyncio
async def test_check_triggers_stop_loss_hit(mock_db, mock_trader):
    # id, ticker, trigger_type, trigger_price, hwm, trailing_pct, action, qty
    mock_db.execute.return_value.fetchall.return_value = [
        ("t1", "AAPL", "stop_loss", 140.0, None, None, "sell", 10)
    ]
    
    monitor = PriceMonitor(mock_trader)
    with patch.object(monitor, "_fetch_prices", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = {"AAPL": 139.5}  # Price below stop-loss
        
        result = await monitor.check_triggers()
        
        assert len(result) == 1
        assert result[0]["ticker"] == "AAPL"
        assert result[0]["trigger_type"] == "stop_loss"
        
        mock_trader.sell.assert_called_once_with(
            ticker="AAPL", qty=10, price=139.5, signal="AUTO_STOP_LOSS"
        )
        
        update_calls = mock_db.execute.call_args_list
        assert any("UPDATE price_triggers SET status = 'triggered'" in str(call) for call in update_calls)

@pytest.mark.asyncio
async def test_check_triggers_take_profit_hit(mock_db, mock_trader):
    """BATTLE-TEST: TP trigger should fire on price exactly equal or above."""
    mock_db.execute.return_value.fetchall.return_value = [
        ("t2", "TSLA", "take_profit", 300.0, None, None, "sell", 5)
    ]
    
    monitor = PriceMonitor(mock_trader)
    with patch.object(monitor, "_fetch_prices", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = {"TSLA": 300.5}  # Price above TP
        
        result = await monitor.check_triggers()
        
        assert len(result) == 1
        assert result[0]["ticker"] == "TSLA"
        mock_trader.sell.assert_called_once_with(
            ticker="TSLA", qty=5, price=300.5, signal="AUTO_TAKE_PROFIT"
        )

@pytest.mark.asyncio
async def test_check_triggers_trailing_stop_updates_hwm(mock_db, mock_trader):
    """BATTLE-TEST: Ensure trailing stop logic correctly ratchets up the HWM and doesn't trigger prematurely."""
    # initial trigger_price = 90.0 (100 - 10%), hwm=100.0, trailing_pct=10.0
    mock_db.execute.return_value.fetchall.return_value = [
        ("t3", "NVDA", "trailing_stop", 90.0, 100.0, 10.0, "sell", 2)
    ]
    
    monitor = PriceMonitor(mock_trader)
    with patch.object(monitor, "_fetch_prices", new_callable=AsyncMock) as mock_fetch:
        # Stock goes up to 120. New HWM = 120. New trigger = 108.
        mock_fetch.return_value = {"NVDA": 120.0} 
        
        result = await monitor.check_triggers()
        
        # Should NOT trigger sell because 120 > 108.
        assert len(result) == 0
        mock_trader.sell.assert_not_called()
        
        # Should have updated the HWM!
        update_calls = mock_db.execute.call_args_list
        hwm_update_happened = False
        for call in update_calls:
            query = call[0][0]
            if "UPDATE price_triggers SET high_water_mark = ?, trigger_price = ?" in query:
                args = call[0][1]
                assert args[0] == 120.0  # new hwm
                assert args[1] == 108.0  # new trigger (120 - 10%)
                hwm_update_happened = True
        assert hwm_update_happened

@pytest.mark.asyncio
async def test_check_triggers_trailing_stop_executes(mock_db, mock_trader):
    """BATTLE-TEST: Ticker price goes down below dynamic trigger price."""
    # hwm=120.0, trigger_price=108.0 (set in previous ratchet)
    mock_db.execute.return_value.fetchall.return_value = [
        ("t3", "NVDA", "trailing_stop", 108.0, 120.0, 10.0, "sell", 2)
    ]
    
    monitor = PriceMonitor(mock_trader)
    with patch.object(monitor, "_fetch_prices", new_callable=AsyncMock) as mock_fetch:
        # Suddenly drops to 105. 
        mock_fetch.return_value = {"NVDA": 105.0} 
        
        result = await monitor.check_triggers()
        
        # 105 < 108 => TRIGGER
        assert len(result) == 1
        assert "Trailing stop hit:" in result[0]["reason"]
        mock_trader.sell.assert_called_once()

@pytest.mark.asyncio
async def test_check_triggers_price_missing_skips_trigger(mock_db, mock_trader):
    """BATTLE-TEST: If one ticker's API fetch fails, don't crash, just skip it."""
    mock_db.execute.return_value.fetchall.return_value = [
        ("t1", "WORK", "stop_loss", 50.0, None, None, "sell", 1),
        ("t2", "FAIL", "take_profit", 100.0, None, None, "sell", 1)  # Imagine API fails for this
    ]
    
    monitor = PriceMonitor(mock_trader)
    with patch.object(monitor, "_fetch_prices", new_callable=AsyncMock) as mock_fetch:
        # FAIL is missing from the returned dict entirely
        mock_fetch.return_value = {"WORK": 45.0} 
        
        result = await monitor.check_triggers()
        
        # Should only execute WORK, skip FAIL entirely without throwing
        assert len(result) == 1
        assert result[0]["ticker"] == "WORK"
        mock_trader.sell.assert_called_once_with(
            ticker="WORK", qty=1, price=45.0, signal="AUTO_STOP_LOSS"
        )

@pytest.mark.asyncio
async def test_check_triggers_bot_id_isolation(mock_db, mock_trader):
    """BATTLE-TEST: Ensure it filters triggers by bot_id, preventing multi-tenant data leaks."""
    monitor = PriceMonitor(mock_trader)
    
    mock_db.execute.return_value.fetchall.return_value = []
    await monitor.check_triggers()
    
    execute_call = mock_db.execute.call_args_list[0][0][0]
    assert "bot_id = ?" in execute_call

@pytest.mark.asyncio
async def test_fetch_prices_parsing_robustness():
    """BATTLE-TEST: yfinance API structure can mutate. Test nested object fallbacks."""
    monitor = PriceMonitor(MagicMock())
    
    class MockFastInfoList(list):
        # Someone replaced fast_info with a list containing a dict?!
        pass
        
    class MockFastInfoDictMissing:
        # Missing lastPrice entirely
        def get(self, key, default=None):
            return default
            
    with patch("yfinance.Ticker") as mock_ticker:
        ticker_instance = MagicMock()
        
        # 1. Provide a completely missing fast_info last price => Should return None not throw
        ticker_instance.fast_info = MockFastInfoDictMissing()
        mock_ticker.return_value = ticker_instance
        prices = await monitor._fetch_prices(["NO_PRICE"])
        assert "NO_PRICE" not in prices or prices["NO_PRICE"] is None
        
        # 2. Provide the absolute worst case: fast_info throws AttributeError
        # (This simulates internet cut out during yfinance instantiation)
        mock_ticker.side_effect = Exception("SSL HANDSHAKE TIMEOUT")
        prices2 = await monitor._fetch_prices(["BROKEN"])
        assert prices2 == {}  # The loop catches and returns an empty dict or valid dicts only.
