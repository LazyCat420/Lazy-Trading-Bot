"""Risk metric computation — pure quant math, no LLM involved.

Computes: Z-Score, Sharpe, Sortino, Calmar, VaR, CVaR, Max Drawdown,
Beta, Alpha, R², Tail Ratio, Ulcer Index, and more from price history.
Requires SPY data for beta/alpha/correlation calculations.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
from scipy import stats as sp_stats

from app.database import get_db
from app.utils.logger import logger


@dataclass
class RiskMetrics:
    """Container for all computed risk metrics."""

    ticker: str
    computed_date: date

    # Core Risk Ratios
    z_score_20: float = 0.0
    z_score_50: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    treynor_ratio: float = 0.0

    # Value at Risk
    var_95: float = 0.0
    var_99: float = 0.0
    cvar_95: float = 0.0
    cvar_99: float = 0.0

    # Drawdown
    max_drawdown: float = 0.0
    max_drawdown_duration_days: int = 0
    current_drawdown: float = 0.0

    # Volatility
    daily_volatility: float = 0.0
    annualized_volatility: float = 0.0
    downside_deviation: float = 0.0
    volatility_skew: float = 0.0
    return_kurtosis: float = 0.0

    # Beta & Correlation (vs SPY)
    beta: float = 1.0
    alpha: float = 0.0
    r_squared: float = 0.0
    correlation_to_spy: float = 0.0

    # Tail Risk
    gain_to_pain_ratio: float = 0.0
    tail_ratio: float = 0.0
    ulcer_index: float = 0.0

    # Information Ratio
    information_ratio: float = 0.0

    def to_dict(self) -> dict:
        """Serialize for API / agent context."""
        return {k: v for k, v in self.__dict__.items()}


class RiskComputer:
    """Computes quantitative risk metrics from stored price history.

    Uses SPY as the market benchmark for beta/alpha/correlation.
    """

    RISK_FREE_RATE = 0.045  # ~4.5% annualized (current T-bill rate)
    TRADING_DAYS_PER_YEAR = 252

    async def compute(
        self,
        ticker: str,
        benchmark: str = "SPY",
    ) -> RiskMetrics:
        """Compute all risk metrics for a ticker.

        Requires at least 60 days of price history for meaningful results.
        Also fetches SPY data for relative metrics (beta, alpha, correlation).
        """
        logger.info("Computing risk metrics for %s", ticker)

        db = get_db()
        today = date.today()

        # Fetch ticker prices
        prices = self._fetch_closes(db, ticker)
        if len(prices) < 60:
            logger.warning(
                "Not enough price data for %s (%d rows, need >= 60)",
                ticker,
                len(prices),
            )
            return RiskMetrics(ticker=ticker, computed_date=today)

        # Fetch benchmark prices (SPY) for relative metrics
        spy_prices = self._fetch_closes(db, benchmark)

        # Align lengths (both must cover same period)
        min_len = min(len(prices), len(spy_prices)) if spy_prices.size > 0 else len(prices)
        prices = prices[-min_len:]
        spy_prices = spy_prices[-min_len:] if spy_prices.size > 0 else np.array([])

        # Daily returns
        returns = np.diff(prices) / prices[:-1]
        spy_returns = (
            np.diff(spy_prices) / spy_prices[:-1]
            if spy_prices.size > 1
            else np.array([])
        )

        daily_rf = self.RISK_FREE_RATE / self.TRADING_DAYS_PER_YEAR

        metrics = RiskMetrics(ticker=ticker, computed_date=today)

        # --- Z-Scores ---
        metrics.z_score_20 = self._z_score(prices, window=20)
        metrics.z_score_50 = self._z_score(prices, window=50)

        # --- Risk/Return Ratios ---
        metrics.sharpe_ratio = self._sharpe(returns, daily_rf)
        metrics.sortino_ratio = self._sortino(returns, daily_rf)
        metrics.calmar_ratio = self._calmar(returns, prices)

        # --- Value at Risk ---
        metrics.var_95 = self._var(returns, 0.05)
        metrics.var_99 = self._var(returns, 0.01)
        metrics.cvar_95 = self._cvar(returns, 0.05)
        metrics.cvar_99 = self._cvar(returns, 0.01)

        # --- Drawdown ---
        metrics.max_drawdown = self._max_drawdown(prices)
        metrics.max_drawdown_duration_days = self._max_dd_duration(prices)
        metrics.current_drawdown = self._current_drawdown(prices)

        # --- Volatility ---
        metrics.daily_volatility = float(np.std(returns, ddof=1))
        metrics.annualized_volatility = metrics.daily_volatility * np.sqrt(
            self.TRADING_DAYS_PER_YEAR
        )
        metrics.downside_deviation = self._downside_dev(returns)
        metrics.volatility_skew = float(sp_stats.skew(returns))
        metrics.return_kurtosis = float(sp_stats.kurtosis(returns))

        # --- Beta / Alpha / Correlation (require benchmark data) ---
        if spy_returns.size > 10:
            metrics.beta = self._beta(returns, spy_returns)
            metrics.alpha = self._alpha(returns, spy_returns, daily_rf)
            metrics.r_squared = self._r_squared(returns, spy_returns)
            metrics.correlation_to_spy = float(
                np.corrcoef(returns, spy_returns)[0, 1]
            )
            metrics.treynor_ratio = self._treynor(returns, daily_rf, metrics.beta)
            metrics.information_ratio = self._information_ratio(
                returns, spy_returns
            )

        # --- Tail Risk ---
        metrics.gain_to_pain_ratio = self._gain_to_pain(returns)
        metrics.tail_ratio = self._tail_ratio(returns)
        metrics.ulcer_index = self._ulcer_index(prices)

        # --- Persist ---
        self._store(db, metrics)

        logger.info(
            "Risk metrics computed for %s: Sharpe=%.2f Sortino=%.2f "
            "MaxDD=%.2f%% Beta=%.2f VaR95=%.4f",
            ticker,
            metrics.sharpe_ratio,
            metrics.sortino_ratio,
            metrics.max_drawdown * 100,
            metrics.beta,
            metrics.var_95,
        )
        return metrics

    # ------------------------------------------------------------------
    # Data fetching
    # ------------------------------------------------------------------
    @staticmethod
    def _fetch_closes(db, ticker: str) -> np.ndarray:
        """Fetch closing prices from price_history table."""
        rows = db.execute(
            "SELECT close FROM price_history WHERE ticker = ? ORDER BY date ASC",
            [ticker],
        ).fetchall()
        return np.array([r[0] for r in rows], dtype=float)

    # ------------------------------------------------------------------
    # Z-Score
    # ------------------------------------------------------------------
    @staticmethod
    def _z_score(prices: np.ndarray, window: int = 20) -> float:
        """How many std devs current price is from rolling mean."""
        if len(prices) < window:
            return 0.0
        recent = prices[-window:]
        mean = np.mean(recent)
        std = np.std(recent, ddof=1)
        return float((prices[-1] - mean) / std) if std > 0 else 0.0

    # ------------------------------------------------------------------
    # Risk/Return Ratios
    # ------------------------------------------------------------------
    @staticmethod
    def _sharpe(returns: np.ndarray, daily_rf: float) -> float:
        """Annualized Sharpe Ratio."""
        excess = returns - daily_rf
        std = np.std(excess, ddof=1)
        if std == 0 or len(excess) == 0:
            return 0.0
        return float((np.mean(excess) / std) * np.sqrt(252))

    @staticmethod
    def _sortino(returns: np.ndarray, daily_rf: float) -> float:
        """Like Sharpe but only penalizes downside volatility."""
        excess = returns - daily_rf
        downside = returns[returns < daily_rf]
        if len(downside) == 0:
            return 0.0
        dd_std = np.std(downside, ddof=1)
        if dd_std == 0:
            return 0.0
        return float((np.mean(excess) / dd_std) * np.sqrt(252))

    @staticmethod
    def _calmar(returns: np.ndarray, prices: np.ndarray) -> float:
        """Annualized return / max drawdown."""
        ann_return = float(np.mean(returns)) * 252
        peak = np.maximum.accumulate(prices)
        dd = (prices - peak) / peak
        max_dd = abs(float(np.min(dd)))
        return ann_return / max_dd if max_dd > 0 else 0.0

    @staticmethod
    def _treynor(returns: np.ndarray, daily_rf: float, beta: float) -> float:
        """Excess return per unit of systematic risk."""
        excess_return = float(np.mean(returns) - daily_rf) * 252
        return excess_return / beta if beta != 0 else 0.0

    # ------------------------------------------------------------------
    # Value at Risk
    # ------------------------------------------------------------------
    @staticmethod
    def _var(returns: np.ndarray, alpha: float) -> float:
        """Historical Value at Risk (parametric)."""
        return float(np.percentile(returns, alpha * 100))

    @staticmethod
    def _cvar(returns: np.ndarray, alpha: float) -> float:
        """Conditional VaR (Expected Shortfall) — avg of losses beyond VaR."""
        var_threshold = np.percentile(returns, alpha * 100)
        tail_losses = returns[returns <= var_threshold]
        return float(np.mean(tail_losses)) if len(tail_losses) > 0 else 0.0

    # ------------------------------------------------------------------
    # Drawdown
    # ------------------------------------------------------------------
    @staticmethod
    def _max_drawdown(prices: np.ndarray) -> float:
        """Maximum peak-to-trough decline as a fraction."""
        peak = np.maximum.accumulate(prices)
        dd = (prices - peak) / peak
        return float(np.min(dd))

    @staticmethod
    def _max_dd_duration(prices: np.ndarray) -> int:
        """Number of trading days in the longest drawdown period."""
        peak = np.maximum.accumulate(prices)
        in_drawdown = prices < peak
        max_dur = 0
        current_dur = 0
        for is_dd in in_drawdown:
            if is_dd:
                current_dur += 1
                max_dur = max(max_dur, current_dur)
            else:
                current_dur = 0
        return max_dur

    @staticmethod
    def _current_drawdown(prices: np.ndarray) -> float:
        """Current drawdown from all-time high."""
        peak = np.max(prices)
        return float((prices[-1] - peak) / peak) if peak > 0 else 0.0

    # ------------------------------------------------------------------
    # Volatility
    # ------------------------------------------------------------------
    @staticmethod
    def _downside_dev(returns: np.ndarray) -> float:
        """Standard deviation of negative returns only."""
        downside = returns[returns < 0]
        return float(np.std(downside, ddof=1)) if len(downside) > 1 else 0.0

    # ------------------------------------------------------------------
    # Beta / Alpha / Correlation
    # ------------------------------------------------------------------
    @staticmethod
    def _beta(returns: np.ndarray, market_returns: np.ndarray) -> float:
        """Beta relative to market benchmark."""
        if len(returns) != len(market_returns) or len(returns) < 2:
            return 1.0
        cov = np.cov(returns, market_returns)
        return float(cov[0, 1] / cov[1, 1]) if cov[1, 1] != 0 else 1.0

    @staticmethod
    def _alpha(
        returns: np.ndarray,
        market_returns: np.ndarray,
        daily_rf: float,
    ) -> float:
        """Jensen's Alpha — annualized excess return above CAPM expected."""
        if len(returns) != len(market_returns) or len(returns) < 2:
            return 0.0
        cov = np.cov(returns, market_returns)
        beta = float(cov[0, 1] / cov[1, 1]) if cov[1, 1] != 0 else 1.0
        ann_return = float(np.mean(returns)) * 252
        market_ann = float(np.mean(market_returns)) * 252
        rf_ann = daily_rf * 252
        return ann_return - (rf_ann + beta * (market_ann - rf_ann))

    @staticmethod
    def _r_squared(returns: np.ndarray, market_returns: np.ndarray) -> float:
        """Coefficient of determination vs market."""
        if len(returns) != len(market_returns) or len(returns) < 2:
            return 0.0
        corr = np.corrcoef(returns, market_returns)[0, 1]
        return float(corr**2)

    @staticmethod
    def _information_ratio(
        returns: np.ndarray,
        benchmark_returns: np.ndarray,
    ) -> float:
        """Active return / tracking error."""
        if len(returns) != len(benchmark_returns) or len(returns) < 2:
            return 0.0
        active = returns - benchmark_returns
        te = np.std(active, ddof=1)
        return float(np.mean(active) / te * np.sqrt(252)) if te > 0 else 0.0

    # ------------------------------------------------------------------
    # Tail Risk
    # ------------------------------------------------------------------
    @staticmethod
    def _gain_to_pain(returns: np.ndarray) -> float:
        """Sum of returns / sum of absolute negative returns."""
        total = float(np.sum(returns))
        pain = float(np.sum(np.abs(returns[returns < 0])))
        return total / pain if pain > 0 else 0.0

    @staticmethod
    def _tail_ratio(returns: np.ndarray) -> float:
        """95th percentile gain / abs(5th percentile loss)."""
        right = float(np.percentile(returns, 95))
        left = abs(float(np.percentile(returns, 5)))
        return right / left if left > 0 else 0.0

    @staticmethod
    def _ulcer_index(prices: np.ndarray) -> float:
        """Measures depth and duration of drawdowns."""
        peak = np.maximum.accumulate(prices)
        dd_pct = ((prices - peak) / peak) * 100
        return float(np.sqrt(np.mean(dd_pct**2)))

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    @staticmethod
    def _store(db, metrics: RiskMetrics) -> None:
        """Store computed risk metrics in DuckDB."""
        db.execute(
            """
            INSERT OR REPLACE INTO risk_metrics
                (ticker, computed_date, z_score_20, z_score_50,
                 sharpe_ratio, sortino_ratio, calmar_ratio, treynor_ratio,
                 var_95, var_99, cvar_95, cvar_99,
                 max_drawdown, max_drawdown_duration_days, current_drawdown,
                 daily_volatility, annualized_volatility, downside_deviation,
                 volatility_skew, return_kurtosis,
                 beta, alpha, r_squared, correlation_to_spy,
                 gain_to_pain_ratio, tail_ratio, ulcer_index,
                 information_ratio)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                metrics.ticker, metrics.computed_date,
                metrics.z_score_20, metrics.z_score_50,
                metrics.sharpe_ratio, metrics.sortino_ratio,
                metrics.calmar_ratio, metrics.treynor_ratio,
                metrics.var_95, metrics.var_99, metrics.cvar_95, metrics.cvar_99,
                metrics.max_drawdown, metrics.max_drawdown_duration_days,
                metrics.current_drawdown,
                metrics.daily_volatility, metrics.annualized_volatility,
                metrics.downside_deviation,
                metrics.volatility_skew, metrics.return_kurtosis,
                metrics.beta, metrics.alpha, metrics.r_squared,
                metrics.correlation_to_spy,
                metrics.gain_to_pain_ratio, metrics.tail_ratio,
                metrics.ulcer_index,
                metrics.information_ratio,
            ],
        )
