"""Price Monitor — checks active triggers against current prices.

Polls yfinance fast_info for live prices, then fires auto-sell orders
when stop-loss, take-profit, or trailing-stop conditions are met.
"""

from __future__ import annotations


from app.database import get_db
from app.utils.logger import logger


class PriceMonitor:
    """Check all active price triggers and fire sells when conditions met."""

    def __init__(self, paper_trader: object) -> None:
        """paper_trader must have a .sell() method."""
        self._trader = paper_trader

    async def check_triggers(self) -> list[dict]:
        """Check all active triggers against current prices.

        Returns a list of triggered actions taken.
        """
        db = get_db()
        triggers = db.execute(
            "SELECT id, ticker, trigger_type, trigger_price, "
            "high_water_mark, trailing_pct, action, qty "
            "FROM price_triggers WHERE status = 'active'"
        ).fetchall()

        if not triggers:
            return []

        # Get unique tickers that need price checks
        tickers = list({t[1] for t in triggers})

        # Fetch current prices
        prices = await self._fetch_prices(tickers)
        if not prices:
            logger.info("[PriceMonitor] No prices fetched, skipping trigger check")
            return []

        actions_taken = []

        for row in triggers:
            trigger_id, ticker, trigger_type, trigger_price = row[0], row[1], row[2], row[3]
            high_water_mark, trailing_pct = row[4], row[5]
            qty = row[7]

            current_price = prices.get(ticker)
            if current_price is None:
                continue

            triggered = False
            reason = ""

            if trigger_type == "stop_loss":
                if current_price <= trigger_price:
                    triggered = True
                    reason = f"Stop-loss hit: ${current_price:.2f} ≤ ${trigger_price:.2f}"

            elif trigger_type == "take_profit":
                if current_price >= trigger_price:
                    triggered = True
                    reason = f"Take-profit hit: ${current_price:.2f} ≥ ${trigger_price:.2f}"

            elif trigger_type == "trailing_stop":
                # Update high-water mark
                if current_price > (high_water_mark or 0):
                    new_hwm = current_price
                    new_trigger = round(new_hwm * (1 - trailing_pct / 100), 2)
                    db.execute(
                        "UPDATE price_triggers SET high_water_mark = ?, trigger_price = ? WHERE id = ?",
                        [new_hwm, new_trigger, trigger_id],
                    )
                    logger.debug(
                        "[PriceMonitor] %s trailing stop: HWM=$%.2f, trigger=$%.2f",
                        ticker, new_hwm, new_trigger,
                    )
                    trigger_price = new_trigger  # Use updated trigger price

                if current_price <= trigger_price:
                    triggered = True
                    reason = (
                        f"Trailing stop hit: ${current_price:.2f} ≤ ${trigger_price:.2f} "
                        f"(HWM=${high_water_mark:.2f})"
                    )

            if triggered:
                logger.info("[PriceMonitor] TRIGGERED %s %s: %s", ticker, trigger_type, reason)

                # Execute the sell
                order = self._trader.sell(
                    ticker=ticker,
                    qty=qty,
                    price=current_price,
                    signal=f"AUTO_{trigger_type.upper()}",
                )

                # Mark trigger as fired
                db.execute(
                    "UPDATE price_triggers SET status = 'triggered' WHERE id = ?",
                    [trigger_id],
                )

                actions_taken.append({
                    "trigger_id": trigger_id,
                    "ticker": ticker,
                    "trigger_type": trigger_type,
                    "trigger_price": trigger_price,
                    "current_price": current_price,
                    "reason": reason,
                    "order_id": order.id if order else None,
                })

        if actions_taken:
            db.commit()
            logger.info(
                "[PriceMonitor] %d triggers fired: %s",
                len(actions_taken),
                [a["ticker"] for a in actions_taken],
            )

        return actions_taken

    # ------------------------------------------------------------------
    # Price fetching
    # ------------------------------------------------------------------

    @staticmethod
    async def _fetch_prices(tickers: list[str]) -> dict[str, float]:
        """Fetch current prices for a list of tickers using yfinance."""
        import asyncio
        import concurrent.futures

        def _fetch_one(symbol: str) -> tuple[str, float | None]:
            try:
                import yfinance as yf
                t = yf.Ticker(symbol)
                price = getattr(t.fast_info, "last_price", None)
                return (symbol, float(price) if price is not None else None)
            except Exception as e:
                logger.warning("[PriceMonitor] Price fetch failed for %s: %s", symbol, e)
                return (symbol, None)

        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(len(tickers), 8)
        ) as pool:
            futures = [loop.run_in_executor(pool, _fetch_one, t) for t in tickers]
            results = await asyncio.gather(*futures, return_exceptions=True)

        prices: dict[str, float] = {}
        for result in results:
            if isinstance(result, tuple) and result[1] is not None:
                prices[result[0]] = result[1]

        return prices
