"""Symbol Filter Pipeline — composable validation for stock tickers.

Every ticker must pass through this pipeline before entering the DB.
Filters run in order; first failure short-circuits.

Usage:
    from app.services.symbol_filter import get_filter_pipeline
    pipeline = get_filter_pipeline()
    result = pipeline.run("$READ", {"source": "discovery"})
    if not result.passed:
        print(f"Rejected: {result.reason}")
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, NamedTuple

from app.database import get_db
from app.utils.logger import logger


# ── Result type returned by every filter ─────────────────────────
class FilterResult(NamedTuple):
    passed: bool
    reason: str   # e.g. "exclusion_list", "format", "user_excluded"
    symbol: str   # normalized form


# ── Individual Filters ───────────────────────────────────────────

class NormalizeFilter:
    """Strip '$', trim, uppercase, collapse whitespace."""

    def apply(self, symbol: str, ctx: dict[str, Any]) -> FilterResult:
        sym = symbol.strip().lstrip("$").strip().upper()
        # Collapse any internal whitespace
        sym = re.sub(r"\s+", "", sym)
        if not sym:
            return FilterResult(False, "empty_after_normalize", sym)
        return FilterResult(True, "", sym)


class FormatFilter:
    """Regex: 1–10 uppercase alphanumerics, dots, hyphens."""

    _PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")

    def apply(self, symbol: str, ctx: dict[str, Any]) -> FilterResult:
        if not self._PATTERN.match(symbol):
            return FilterResult(
                False, f"bad_format:{symbol}", symbol,
            )
        return FilterResult(True, "", symbol)


class ExclusionListFilter:
    """Reuse TickerValidator.EXCLUSION_LIST — instant O(1) lookup."""

    def __init__(self) -> None:
        from app.services.ticker_validator import TickerValidator
        self._words: set[str] = TickerValidator.EXCLUSION_LIST

    def apply(self, symbol: str, ctx: dict[str, Any]) -> FilterResult:
        if symbol in self._words:
            return FilterResult(False, "exclusion_list", symbol)
        return FilterResult(True, "", symbol)


class ForeignExchangeFilter:
    """Reject non-US exchange suffixes (.MX, .L, .TO, etc)."""

    _FOREIGN_SUFFIXES = {
        ".MX", ".L", ".TO", ".SA", ".DE", ".PA", ".HK",
        ".SS", ".SZ", ".TW", ".AX", ".NS", ".BO", ".KS",
    }

    def apply(self, symbol: str, ctx: dict[str, Any]) -> FilterResult:
        for suffix in self._FOREIGN_SUFFIXES:
            if symbol.endswith(suffix):
                return FilterResult(
                    False, f"foreign_exchange:{suffix}", symbol,
                )
        return FilterResult(True, "", symbol)


class UserExclusionFilter:
    """Check user_exclusions table (bot-scoped)."""

    def apply(self, symbol: str, ctx: dict[str, Any]) -> FilterResult:
        bot_id = ctx.get("bot_id", "default")
        svc = UserExclusionsService()
        if svc.is_excluded(symbol, bot_id):
            return FilterResult(False, "user_excluded", symbol)
        return FilterResult(True, "", symbol)


class AssetCheckFilter:
    """yFinance fast_info — has price > 0?

    Auto-blacklists tickers that fail so they're never re-scraped.
    """

    _cache: dict[str, bool] = {}

    def apply(self, symbol: str, ctx: dict[str, Any]) -> FilterResult:
        if symbol in self._cache:
            if not self._cache[symbol]:
                return FilterResult(
                    False, "no_market_data", symbol,
                )
            return FilterResult(True, "", symbol)

        try:
            import yfinance as yf

            stock = yf.Ticker(symbol)
            fi = stock.fast_info
            price = getattr(fi, "last_price", None)
            if price is None or price <= 0:
                self._cache[symbol] = False
                BlacklistService.auto_blacklist(
                    symbol, "no_market_data", ctx.get("source", "asset_check"),
                )
                return FilterResult(
                    False, "no_market_data", symbol,
                )
            self._cache[symbol] = True
            return FilterResult(True, "", symbol)
        except Exception:
            self._cache[symbol] = False
            BlacklistService.auto_blacklist(
                symbol, "yfinance_error", ctx.get("source", "asset_check"),
            )
            return FilterResult(
                False, "yfinance_error", symbol,
            )


class BlacklistFilter:
    """Check the persistent ticker_blacklist table — O(1) via in-memory cache.

    Once a ticker fails yfinance validation, it's auto-blacklisted in DuckDB
    and never re-scraped, even after restarts.
    """

    _cache: set[str] | None = None

    def apply(self, symbol: str, ctx: dict[str, Any]) -> FilterResult:
        if BlacklistFilter._cache is None:
            BlacklistFilter._load_cache()
        if symbol in BlacklistFilter._cache:
            return FilterResult(False, "blacklisted", symbol)
        return FilterResult(True, "", symbol)

    @staticmethod
    def _load_cache() -> None:
        """Load blacklisted tickers from DuckDB into memory."""
        try:
            db = get_db()
            rows = db.execute(
                "SELECT symbol FROM ticker_blacklist"
            ).fetchall()
            BlacklistFilter._cache = {r[0] for r in rows}
            logger.info(
                "[Blacklist] Loaded %d blacklisted tickers",
                len(BlacklistFilter._cache),
            )
        except Exception:
            BlacklistFilter._cache = set()

    @staticmethod
    def invalidate_cache() -> None:
        """Force re-read from DB on next apply()."""
        BlacklistFilter._cache = None


# ── Pipeline ─────────────────────────────────────────────────────

class FilterPipeline:
    """Run filters in sequence; first failure short-circuits."""

    def __init__(self, filters: list[Any]) -> None:
        self._filters = filters

    def run(
        self, symbol: str, ctx: dict[str, Any] | None = None,
    ) -> FilterResult:
        """Run all filters on a single symbol."""
        if ctx is None:
            ctx = {}
        current = symbol
        for f in self._filters:
            result = f.apply(current, ctx)
            if not result.passed:
                _log_rejection(current, result.reason, ctx)
                return result
            # Carry the (possibly normalized) symbol forward
            current = result.symbol
        return FilterResult(True, "", current)

    def run_batch(
        self,
        symbols: list[str],
        ctx: dict[str, Any] | None = None,
    ) -> tuple[list[str], list[tuple[str, str]]]:
        """Filter a batch. Returns (passed[], rejected[(sym, reason)])."""
        passed: list[str] = []
        rejected: list[tuple[str, str]] = []
        for sym in symbols:
            r = self.run(sym, ctx)
            if r.passed:
                passed.append(r.symbol)
            else:
                rejected.append((sym, r.reason))
        return passed, rejected


# ── Singleton ────────────────────────────────────────────────────

_pipeline: FilterPipeline | None = None


def get_filter_pipeline() -> FilterPipeline:
    """Return the singleton pipeline (lazy init)."""
    global _pipeline
    if _pipeline is None:
        _pipeline = FilterPipeline([
            NormalizeFilter(),
            FormatFilter(),
            ForeignExchangeFilter(),
            ExclusionListFilter(),
            UserExclusionFilter(),
            BlacklistFilter(),      # Persistent DB blacklist (before yfinance)
            AssetCheckFilter(),     # yfinance check (auto-blacklists failures)
        ])
        # Pre-seed the AssetCheckFilter cache with tickers already validated
        # (watchlist + any ticker with price data in DB). This avoids
        # redundant yfinance calls for tickers we already know are real.
        _preseed_validated_cache()
    return _pipeline


def _preseed_validated_cache() -> None:
    """Load known-valid tickers into AssetCheckFilter cache on startup."""
    try:
        db = get_db()
        # Tickers with price history already passed yfinance validation
        rows = db.execute(
            "SELECT DISTINCT ticker FROM price_history"
        ).fetchall()
        known = {r[0] for r in rows}

        # Also include watchlist tickers
        try:
            wl_rows = db.execute(
                "SELECT DISTINCT ticker FROM watchlist"
            ).fetchall()
            known.update(r[0] for r in wl_rows)
        except Exception:
            pass  # watchlist table may not exist yet

        if known:
            for sym in known:
                AssetCheckFilter._cache[sym] = True
            logger.info(
                "[Filter] Pre-seeded %d validated tickers into cache",
                len(known),
            )
    except Exception as e:
        logger.debug("[Filter] Pre-seed failed (non-critical): %s", e)


# ── Rejection quarantine logger ──────────────────────────────────

def _log_rejection(
    symbol: str, reason: str, ctx: dict[str, Any],
) -> None:
    """Log to rejected_symbols table + stdout."""
    logger.debug(
        "[Filter] %s REJECTED — %s (source=%s)",
        symbol, reason, ctx.get("source", "?"),
    )
    try:
        db = get_db()
        db.execute(
            """
            INSERT INTO rejected_symbols
                (symbol, source, reason, raw_context, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                symbol,
                ctx.get("source", "unknown"),
                reason,
                str(ctx.get("raw", ""))[:500],
                datetime.now(),
            ],
        )
    except Exception:
        pass  # Table may not exist yet during migration


# ── User Exclusions Service ──────────────────────────────────────

class UserExclusionsService:
    """CRUD for the user_exclusions table."""

    def exclude(
        self,
        symbol: str,
        bot_id: str = "default",
        reason: str = "user_deleted",
    ) -> None:
        """Add a symbol to the exclusion list."""
        symbol = symbol.upper().strip()
        db = get_db()
        # Upsert
        existing = db.execute(
            "SELECT symbol FROM user_exclusions "
            "WHERE symbol = ? AND bot_id = ?",
            [symbol, bot_id],
        ).fetchone()
        if existing:
            return  # Already excluded
        db.execute(
            """
            INSERT INTO user_exclusions
                (symbol, bot_id, reason, created_by, created_at)
            VALUES (?, ?, ?, 'user', ?)
            """,
            [symbol, bot_id, reason, datetime.now()],
        )
        db.commit()
        logger.info(
            "[Exclusions] Excluded %s for bot=%s (%s)",
            symbol, bot_id, reason,
        )

    def restore(
        self, symbol: str, bot_id: str = "default",
    ) -> bool:
        """Remove a symbol from the exclusion list. Returns True if found."""
        symbol = symbol.upper().strip()
        db = get_db()
        deleted = db.execute(
            "DELETE FROM user_exclusions "
            "WHERE symbol = ? AND bot_id = ?",
            [symbol, bot_id],
        ).rowcount
        db.commit()
        if deleted:
            logger.info(
                "[Exclusions] Restored %s for bot=%s", symbol, bot_id,
            )
        return bool(deleted)

    def list_exclusions(
        self, bot_id: str = "default",
    ) -> list[dict[str, Any]]:
        """Return all excluded symbols for a bot."""
        db = get_db()
        rows = db.execute(
            "SELECT symbol, reason, created_at "
            "FROM user_exclusions WHERE bot_id = ? "
            "ORDER BY created_at DESC",
            [bot_id],
        ).fetchall()
        return [
            {
                "symbol": r[0],
                "reason": r[1],
                "created_at": str(r[2]),
            }
            for r in rows
        ]

    def is_excluded(
        self, symbol: str, bot_id: str = "default",
    ) -> bool:
        """Check if a symbol is excluded."""
        db = get_db()
        row = db.execute(
            "SELECT 1 FROM user_exclusions "
            "WHERE symbol = ? AND bot_id = ?",
            [symbol, bot_id],
        ).fetchone()
        return row is not None


# ── Blacklist Service ────────────────────────────────────────────

class BlacklistService:
    """Manages the persistent ticker_blacklist table."""

    @staticmethod
    def auto_blacklist(
        symbol: str,
        reason: str,
        source: str = "auto",
    ) -> None:
        """Add a ticker to the blacklist (silently skips duplicates)."""
        try:
            db = get_db()
            db.execute(
                """
                INSERT OR IGNORE INTO ticker_blacklist
                    (symbol, reason, source, created_at)
                VALUES (?, ?, ?, ?)
                """,
                [symbol, reason, source, datetime.now()],
            )
            db.commit()
            # Update in-memory cache
            if BlacklistFilter._cache is not None:
                BlacklistFilter._cache.add(symbol)
            logger.info(
                "[Blacklist] Auto-blacklisted %s — %s (source=%s)",
                symbol, reason, source,
            )
        except Exception as exc:
            logger.debug("[Blacklist] Failed to blacklist %s: %s", symbol, exc)

    @staticmethod
    def remove(symbol: str) -> bool:
        """Remove a ticker from the blacklist. Returns True if found."""
        symbol = symbol.upper().strip()
        try:
            db = get_db()
            deleted = db.execute(
                "DELETE FROM ticker_blacklist WHERE symbol = ?",
                [symbol],
            ).rowcount
            db.commit()
            if deleted:
                # Update in-memory cache
                if BlacklistFilter._cache is not None:
                    BlacklistFilter._cache.discard(symbol)
                logger.info("[Blacklist] Removed %s from blacklist", symbol)
            return bool(deleted)
        except Exception:
            return False

    @staticmethod
    def list_blacklisted() -> list[dict[str, Any]]:
        """Return all blacklisted symbols."""
        try:
            db = get_db()
            rows = db.execute(
                "SELECT symbol, reason, source, created_at "
                "FROM ticker_blacklist ORDER BY created_at DESC"
            ).fetchall()
            return [
                {
                    "symbol": r[0],
                    "reason": r[1],
                    "source": r[2],
                    "created_at": str(r[3]),
                }
                for r in rows
            ]
        except Exception:
            return []

    @staticmethod
    def is_blacklisted(symbol: str) -> bool:
        """Check if a symbol is blacklisted."""
        if BlacklistFilter._cache is None:
            BlacklistFilter._load_cache()
        return symbol.upper().strip() in BlacklistFilter._cache
