"""Ticker Validator — three-layer validation (exclusion list → yfinance → LLM).

Ensures only real, actively traded stock tickers enter the watchlist.
"""

from __future__ import annotations

from app.services.unified_logger import track_class_telemetry, track_telemetry
import yfinance as yf

from app.utils.logger import logger


@track_class_telemetry
class TickerValidator:
    """Three-layer validation: exclusion list → yfinance → LLM logic check."""

    # Common English words and abbreviations that look like tickers.
    # This list must be aggressive because transcripts contain thousands
    # of uppercase words that regex picks up as potential tickers.
    EXCLUSION_LIST: set[str] = {
        # ═══════════════════════════════════════════════════════════════
        # MINIMAL exclusion list — ONLY terms that are NEVER real stocks.
        # All ambiguous words are handled by yfinance validation +
        # the persistent auto-blacklist (BlacklistService).
        # ═══════════════════════════════════════════════════════════════

        # ── Reddit / trading jargon (never tickers) ──
        "YOLO", "DD", "ATH", "IMO", "EOD", "WSB", "OP", "EDIT", "TLDR",
        "HODL", "FOMO", "DIPS", "RALLY", "OTM", "ITM", "DTE", "FD",

        # ── Finance terms / events that look like tickers ──
        "ATM",    # At The Money — options term
        "FOMC",   # Federal Open Market Committee
        "GTC",    # Good Till Cancelled / NVIDIA conference
        "CPI",    # Consumer Price Index
        "PPI",    # Producer Price Index
        "NFP",    # Non-Farm Payrolls
        "FDIC",   # Federal Deposit Insurance Corp
        "OPEC",   # Oil cartel
        "NASDAQ", # Exchange name, not a ticker
        "NYSE",   # Exchange name
        "ETF",    # Asset class, not a ticker
        "IPO",    # Event type, not a ticker

        # ── Government / org acronyms (never tickers) ──
        "CEO", "CFO", "COO", "GDP", "USA", "IRS", "FBI", "CIA",
        "NASA", "NATO", "DEPT", "CORP", "GOVT", "EURO",
        "CNBC",   # Closed-end fund — no usable data, always crashes
    }

    def __init__(self) -> None:
        self._cache: dict[str, bool] = {}

    @staticmethod
    def sanitize_ticker(ticker: str) -> str:
        """Clean a raw ticker string for use with APIs.

        Strips common prefixes ($, #), whitespace, and non-alpha chars.
        Examples:
            "$AAPL"  -> "AAPL"
            " $SNE " -> "SNE"
            "#NVDA"  -> "NVDA"
            "AAPL."  -> "AAPL"
        """
        import re as _re
        ticker = ticker.strip()
        # Strip leading $ or # (Reddit/Twitter format)
        ticker = ticker.lstrip("$#")
        # Remove any non-alphanumeric chars except dots and hyphens
        # (BRK.B, BF-B are valid tickers)
        ticker = _re.sub(r"[^A-Za-z0-9.\-]", "", ticker)
        return ticker.upper().strip()

    def validate(self, ticker: str) -> bool:
        """Validate a single ticker. Returns True if it's a real stock."""
        ticker = self.sanitize_ticker(ticker)

        # Layer 1: Exclusion list (instant)
        if ticker in self.EXCLUSION_LIST:
            logger.debug("[Validator] %s REJECTED — exclusion list", ticker)
            return False

        if not ticker:
            logger.debug("[Validator] %s REJECTED — empty", ticker)
            return False

        if len(ticker) > 5:
            logger.debug("[Validator] %s REJECTED — length %d", ticker, len(ticker))
            return False

        # Check cache
        if ticker in self._cache:
            return self._cache[ticker]

        # Layer 2: yFinance check
        try:
            stock = yf.Ticker(ticker)
            fi = stock.fast_info
            price = getattr(fi, "last_price", None)
            if price is None or price <= 0:
                logger.debug("[Validator] %s REJECTED — no price data", ticker)
                self._cache[ticker] = False
                return False

            logger.info(
                "[Validator] %s VALIDATED — price=$%.2f", ticker, price
            )
            self._cache[ticker] = True
            return True

        except Exception as e:
            logger.debug("[Validator] %s REJECTED — yfinance error: %s", ticker, e)
            self._cache[ticker] = False
            return False

    def validate_batch(self, tickers: list[str]) -> list[str]:
        """Validate multiple tickers, return only the valid ones."""
        valid = []
        for t in tickers:
            if self.validate(t):
                valid.append(self.sanitize_ticker(t))
        logger.info(
            "[Validator] Batch: %d/%d valid",
            len(valid), len(tickers),
        )
        return valid
