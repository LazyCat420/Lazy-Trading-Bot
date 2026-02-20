"""Layer 1 — Quant Signal Engine.

Pure math, zero LLM calls.  Reads stored Phase-1 data from DuckDB and
produces a QuantScorecard for each ticker.

Original metrics (Sharpe, Sortino, Calmar, VaR, CVaR, MaxDrawdown,
Z-Score, Robust Z-Score, Bollinger %B, Omega, Kelly):
  Reused from RiskComputer storage.

PhD-Level additions (Phase 1A):
  • Momentum Factor (Jegadeesh & Titman 1993)
  • Mean Reversion Score
  • Hurst Exponent (R/S analysis)
  • VWAP Deviation
  • Fama-French Alpha (simplified: market + size + value)
  • Earnings Yield Gap (Fed Model)
  • Altman Z-Score (bankruptcy predictor)
  • Piotroski F-Score (9-point financial health)
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

import numpy as np

from app.database import get_db
from app.models.dossier import QuantScorecard
from app.utils.logger import logger


# ---------------------------------------------------------------------------
# Market cap tier classification
# ---------------------------------------------------------------------------

def classify_cap_tier(market_cap: float) -> str:
    """Classify market cap into standard institutional tiers."""
    if market_cap >= 200e9:
        return "mega"      # >$200B  (AAPL, MSFT, AMZN)
    if market_cap >= 10e9:
        return "large"     # $10B-$200B
    if market_cap >= 2e9:
        return "mid"       # $2B-$10B
    if market_cap >= 300e6:
        return "small"     # $300M-$2B
    if market_cap >= 50e6:
        return "micro"     # $50M-$300M
    return "nano"          # <$50M


# ---------------------------------------------------------------------------
# Pure quant helper functions (original)
# ---------------------------------------------------------------------------

def robust_z_score(prices: list[float], window: int = 20) -> float:
    """MAD-based Z-score — less sensitive to fat-tail crashes."""
    if len(prices) < window:
        return 0.0
    recent = np.array(prices[-window:])
    median = np.median(recent)
    q75, q25 = np.percentile(recent, [75, 25])
    iqr = q75 - q25
    if iqr <= 0:
        return 0.0
    return float((prices[-1] - median) / (iqr * 0.7413))


def compute_trend_template_score(
    current_price: float,
    sma_50: float,
    sma_150: float,
    sma_200: float,
    high_52w: float,
    low_52w: float,
    rs_rating: float,
) -> float:
    """Mark Minervini's Trend Template Score (Stage 2 Uptrend).

    Score ranges from 0 to 100 based on fulfilled criteria:
    1. Base Criteria (Max 50 pts):
       - Price > SMA50 > SMA150 > SMA200 (Uptrend alignment)
       - Price > 52w Low + 25% (Off lows)
       - Price within 25% of 52w High (Near highs)
    2. Power Criteria (Max 50 pts):
       - RS Rating > 70 (Outperformance)
       - SMA200 trending up (we proxy this with current price > SMA200 * 1.05)
    """
    score = 0.0
    if not (current_price > 0 and sma_200 > 0):
        return 0.0

    # 1. Alignment (30 pts)
    if current_price > sma_50 > sma_150 > sma_200:
        score += 30
    elif current_price > sma_150 > sma_200:
        score += 20  # Partial alignment
    elif current_price > sma_200:
        score += 10  # Above long-term trend

    # 2. Proximity to Highs/Lows (20 pts)
    # At least 25% above 52w Low
    if low_52w > 0 and current_price > low_52w * 1.25:
        score += 10
    # Within 25% of 52w High
    if high_52w > 0 and current_price > high_52w * 0.75:
        score += 10

    # 3. Strength (50 pts)
    # RS Rating (proxy via momentum percentile)
    if rs_rating > 90:
        score += 30
    elif rs_rating > 80:
        score += 20
    elif rs_rating > 70:
        score += 10

    # Strong Uptrend Confirmation (Price > 5% above SMA200)
    if current_price > sma_200 * 1.05:
        score += 20

    return min(100.0, score)


def compute_vcp_score(
    natr: float,
    volume_contraction: bool,
    bollinger_bandwidth: float,
) -> float:
    """Volatility Contraction Pattern (VCP) Score.

    Target: Tight price action + drying volume.
    Max Score: 100
    """
    score = 0.0

    # 1. Volatility (NATR) - Lower is better for tightness
    # Ideal VCP has NATR < 3.0 (3% daily range)
    if natr < 2.0:
        score += 40
    elif natr < 3.0:
        score += 25
    elif natr < 4.0:
        score += 10

    # 2. Bollinger Bandwidth (Squeeze)
    # Bandwidth < 0.10 (10%) is very tight
    if bollinger_bandwidth < 0.10:
        score += 30
    elif bollinger_bandwidth < 0.20:
        score += 15

    # 3. Volume Contraction (Dry Up)
    if volume_contraction:
        score += 30

    return min(100.0, score)


def bollinger_pct_b(price: float, upper: float, lower: float) -> float:
    """Position within Bollinger Bands.  >1 = above upper, <0 = below lower."""
    band_width = upper - lower
    if band_width <= 0:
        return 0.5
    return float((price - lower) / band_width)


def percentile_rank(values: list[float], current: float) -> float:
    """Non-parametric: what % of historical values are below *current*?"""
    if not values:
        return 50.0
    return float(sum(1 for v in values if v < current) / len(values) * 100)


def omega_ratio(returns: np.ndarray, threshold: float = 0.0) -> float:
    """Probability-weighted gains vs losses.  Captures skew + kurtosis."""
    excess = returns - threshold
    gains = float(np.sum(excess[excess > 0]))
    losses = float(np.abs(np.sum(excess[excess < 0])))
    if losses == 0:
        return 99.0  # cap at a practical maximum
    return min(gains / losses, 99.0)


def kelly_fraction(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """Optimal fraction of capital to allocate (full Kelly)."""
    if avg_loss == 0:
        return 0.0
    b = avg_win / avg_loss  # payoff ratio
    f = (b * win_rate - (1 - win_rate)) / b
    return max(0.0, min(f, 1.0))  # clamp [0, 1]


def _win_loss_stats(returns: np.ndarray) -> tuple[float, float, float]:
    """Compute win rate, avg win, avg loss from daily returns."""
    wins = returns[returns > 0]
    losses = returns[returns < 0]
    win_rate = len(wins) / len(returns) if len(returns) > 0 else 0.5
    avg_win = float(np.mean(wins)) if len(wins) > 0 else 0.0
    avg_loss = float(np.abs(np.mean(losses))) if len(losses) > 0 else 0.0
    return win_rate, avg_win, avg_loss


# ---------------------------------------------------------------------------
# PhD-Level quant functions (Phase 1A)
# ---------------------------------------------------------------------------

def momentum_factor(closes: list[float], lookback: int = 252) -> float:
    """12-month price momentum (Jegadeesh & Titman 1993).

    Returns fractional return over `lookback` trading days.
    Uses skip-month convention: excludes most recent 21 days to avoid
    short-term reversal noise.
    """
    if len(closes) < lookback:
        # Use whatever history we have
        if len(closes) < 42:  # need at least 2 months
            return 0.0
        return float((closes[-22] / closes[0]) - 1.0)
    return float((closes[-22] / closes[-lookback]) - 1.0)


def mean_reversion_score(closes: list[float], window: int = 50) -> float:
    """Distance from SMA-50 in standard deviations.

    Positive = overbought, negative = oversold.
    |score| > 2.0 signals extreme deviation.
    """
    if len(closes) < window:
        return 0.0
    recent = np.array(closes[-window:])
    sma = float(np.mean(recent))
    std = float(np.std(recent))
    if std <= 0:
        return 0.0
    return float((closes[-1] - sma) / std)


def hurst_exponent(closes: list[float], max_lag: int = 100) -> float:
    """Simplified Hurst exponent via R/S analysis.

    H > 0.5 → trending (momentum strategy favored)
    H = 0.5 → random walk
    H < 0.5 → mean-reverting (contrarian strategy favored)
    """
    if len(closes) < max_lag:
        return 0.5  # default: random walk assumption

    ts = np.array(closes[-max_lag:])
    lags = range(2, min(max_lag // 2, 20) + 1)
    rs_values = []

    for lag in lags:
        # Split into sub-series of length `lag`
        n_sub = len(ts) // lag
        if n_sub < 1:
            continue

        rs_list = []
        for i in range(n_sub):
            sub = ts[i * lag : (i + 1) * lag]
            mean_sub = np.mean(sub)
            deviations = np.cumsum(sub - mean_sub)
            r = float(np.max(deviations) - np.min(deviations))
            s = float(np.std(sub))
            if s > 0:
                rs_list.append(r / s)

        if rs_list:
            rs_values.append((np.log(lag), np.log(np.mean(rs_list))))

    if len(rs_values) < 3:
        return 0.5

    log_lags, log_rs = zip(*rs_values)
    # Linear regression: log(R/S) = H * log(n) + c
    coeffs = np.polyfit(log_lags, log_rs, 1)
    h = float(coeffs[0])
    return max(0.0, min(h, 1.0))  # clamp to [0, 1]


def vwap_deviation(closes: list[float], volumes: list[float],
                    window: int = 20) -> float:
    """Deviation from Volume-Weighted Average Price.

    Positive = price above VWAP (institutional buying pressure)
    Negative = price below VWAP (institutional selling pressure)
    """
    if len(closes) < window or len(volumes) < window:
        return 0.0

    c = np.array(closes[-window:])
    v = np.array(volumes[-window:])
    total_vol = float(np.sum(v))
    if total_vol <= 0:
        return 0.0

    vwap = float(np.sum(c * v) / total_vol)
    if vwap <= 0:
        return 0.0
    return float((closes[-1] - vwap) / vwap)


def earnings_yield_gap(trailing_pe: float,
                        treasury_10y: float = 0.043) -> float:
    """Equity Risk Premium via Fed Model.

    E/P - Treasury 10Y yield.
    Positive = stocks cheap vs bonds.
    Negative = stocks expensive vs bonds.

    Default treasury rate: ~4.3% (approximate recent US 10Y).
    """
    if trailing_pe <= 0:
        return 0.0
    ep_ratio = 1.0 / trailing_pe
    return float(ep_ratio - treasury_10y)


def altman_z_score(
    working_capital: float,
    retained_earnings: float,
    ebit: float,
    market_cap: float,
    total_liabilities: float,
    revenue: float,
    total_assets: float,
) -> float:
    """Altman Z-Score — bankruptcy predictor (1968).

    Z > 2.99  → Safe zone
    1.81 < Z < 2.99 → Grey zone
    Z < 1.81  → Distress zone

    Formula: 1.2*X1 + 1.4*X2 + 3.3*X3 + 0.6*X4 + 1.0*X5
    """
    if total_assets <= 0:
        return 0.0

    x1 = working_capital / total_assets            # liquidity
    x2 = retained_earnings / total_assets           # profitability leverage
    x3 = ebit / total_assets                        # asset productivity
    x4 = market_cap / max(total_liabilities, 1.0)   # solvency
    x5 = revenue / total_assets                     # efficiency

    z = 1.2 * x1 + 1.4 * x2 + 3.3 * x3 + 0.6 * x4 + 1.0 * x5
    return float(z)


def piotroski_f_score(
    *,
    # Current year
    net_income: float,
    operating_cf: float,
    roa_current: float,
    roa_previous: float,
    # Leverage
    debt_current: float,
    debt_previous: float,
    current_ratio_current: float,
    current_ratio_previous: float,
    # Operating efficiency
    gross_margin_current: float,
    gross_margin_previous: float,
    asset_turnover_current: float,
    asset_turnover_previous: float,
    # Dilution
    shares_current: float = 0.0,
    shares_previous: float = 0.0,
) -> int:
    """Piotroski F-Score — 9-point financial health checklist.

    Each criterion adds 1 point:
    1. Positive net income
    2. Positive operating cash flow
    3. ROA improving
    4. Quality of earnings (OCF > NI)
    5. Decreasing leverage (debt/assets)
    6. Improving liquidity (current ratio)
    7. No share dilution
    8. Improving gross margin
    9. Improving asset turnover
    """
    score = 0

    # Profitability signals (4 points)
    if net_income > 0:
        score += 1
    if operating_cf > 0:
        score += 1
    if roa_current > roa_previous:
        score += 1
    if operating_cf > net_income:  # earnings quality
        score += 1

    # Leverage/liquidity signals (3 points)
    if debt_current < debt_previous:
        score += 1
    if current_ratio_current > current_ratio_previous:
        score += 1
    if shares_current <= shares_previous or shares_previous <= 0:
        score += 1  # no dilution

    # Operating efficiency signals (2 points)
    if gross_margin_current > gross_margin_previous:
        score += 1
    if asset_turnover_current > asset_turnover_previous:
        score += 1

    return score


# ---------------------------------------------------------------------------
# Anomaly flag detection (expanded)
# ---------------------------------------------------------------------------

def generate_flags(
    scorecard: QuantScorecard,
    days_until_earnings: int | None = None,
    net_insider_buying: float = 0.0,
) -> list[str]:
    """Auto-detect anomalies that warrant Layer-2 follow-up questions."""
    flags: list[str] = []

    # Price extremes
    if abs(scorecard.z_score_20d) > 2.0:
        side = "high" if scorecard.z_score_20d > 0 else "low"
        flags.append(f"z_score_{side}")
    if scorecard.bollinger_pct_b > 1.0:
        flags.append("price_above_upper_band")
    elif scorecard.bollinger_pct_b < 0.0:
        flags.append("price_below_lower_band")

    # Volume spike
    if scorecard.percentile_rank_volume > 95:
        flags.append("volume_spike_95th")

    # Risk
    if scorecard.max_drawdown < -0.20:
        flags.append("drawdown_exceeds_20pct")
    if scorecard.calmar_ratio > 3.0:
        flags.append("exceptional_calmar")
    if scorecard.sortino_ratio < 0:
        flags.append("negative_sortino")

    # Earnings proximity
    if days_until_earnings is not None and 0 < days_until_earnings <= 5:
        flags.append(f"earnings_in_{days_until_earnings}_days")

    # Insider activity
    if net_insider_buying > 500_000:
        flags.append("insider_buying_spike")
    elif net_insider_buying < -500_000:
        flags.append("insider_selling_spike")

    # ── PhD-level flags ──
    # Momentum extremes
    if scorecard.momentum_12m > 0.50:
        flags.append("strong_momentum_up")
    elif scorecard.momentum_12m < -0.30:
        flags.append("strong_momentum_down")

    # Mean reversion opportunity
    if abs(scorecard.mean_reversion_score) > 2.0:
        direction = "overbought" if scorecard.mean_reversion_score > 0 else "oversold"
        flags.append(f"mean_reversion_{direction}")

    # Hurst exponent regime
    if scorecard.hurst_exponent > 0.65:
        flags.append("strong_trend_regime")
    elif scorecard.hurst_exponent < 0.35:
        flags.append("mean_reverting_regime")

    # Bankruptcy risk
    if scorecard.altman_z_score > 0:  # only flag if computed
        if scorecard.altman_z_score < 1.81:
            flags.append("bankruptcy_risk_high")
        elif scorecard.altman_z_score < 2.99:
            flags.append("bankruptcy_risk_grey_zone")

    # Piotroski
    if scorecard.piotroski_f_score >= 8:
        flags.append("piotroski_strong")
    elif 0 < scorecard.piotroski_f_score <= 2:
        flags.append("piotroski_weak")

    # Earnings yield gap
    if scorecard.earnings_yield_gap > 0.04:
        flags.append("cheap_vs_bonds")
    elif scorecard.earnings_yield_gap < -0.02:
        flags.append("expensive_vs_bonds")

    return flags


# ---------------------------------------------------------------------------
# Main engine class
# ---------------------------------------------------------------------------

class QuantSignalEngine:
    """Compute a QuantScorecard for a ticker from Phase-1 DuckDB data."""

    def compute(self, ticker: str) -> QuantScorecard:
        """Run all quant computations and return a filled scorecard."""
        db = get_db()
        now = datetime.now()

        # ── Fetch price history ────────────────────────────────────
        rows = db.execute(
            "SELECT open, high, low, close, volume FROM price_history "
            "WHERE ticker = ? ORDER BY date ASC",
            [ticker],
        ).fetchall()

        if len(rows) < 20:
            logger.warning("[Quant] %s has only %d rows — too few", ticker, len(rows))
            return QuantScorecard(ticker=ticker, computed_at=now, flags=["insufficient_data"])

        # Unpack columns
        opens = [float(r[0]) for r in rows]
        highs = [float(r[1]) for r in rows]
        lows = [float(r[2]) for r in rows]
        closes = [float(r[3]) for r in rows]
        volumes = [float(r[4]) for r in rows if r[4] is not None]
        returns = np.diff(np.log(np.array(closes)))  # log returns

        # ── Calculate Technicals (On-the-fly) ──────────────────────
        # SMAs
        sma_50 = float(np.mean(closes[-50:])) if len(closes) >= 50 else 0.0
        sma_150 = float(np.mean(closes[-150:])) if len(closes) >= 150 else 0.0
        sma_200 = float(np.mean(closes[-200:])) if len(closes) >= 200 else 0.0

        # 52-Week Range
        window_52w = 252
        if len(closes) >= window_52w:
            high_52w = float(np.max(closes[-window_52w:]))
            low_52w = float(np.min(closes[-window_52w:]))
        else:
            high_52w = float(np.max(closes)) if closes else 0.0
            low_52w = float(np.min(closes)) if closes else 0.0

        # ATR / NATR (14-day)
        if len(closes) > 15:
            tr_list = []
            for i in range(1, len(closes)):
                h, l, c_prev = highs[i], lows[i], closes[i-1]
                tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
                tr_list.append(tr)
            atr_14 = float(np.mean(tr_list[-14:]))
            natr_14 = (atr_14 / closes[-1]) * 100
        else:
            natr_14 = 5.0  # Default fallback

        # Volume Contraction (vs 50-day avg)
        avg_vol_50 = float(np.mean(volumes[-50:])) if len(volumes) >= 50 else 1.0
        curr_vol = float(np.mean(volumes[-5:])) if volumes else 1.0
        vol_contracting = curr_vol < avg_vol_50

        # Bollinger Bandwidth
        # Re-use fetched Upper/Lower or calculate? Let's use fetched if available, or calc
        # For consistency with Phase 1A, let's stick to fetched for Bollinger
        # but calculate bandwidth here.

        # ── Fetch stored risk metrics ──────────────────────────────
        risk_row = db.execute(
            "SELECT z_score_20, sharpe_ratio, sortino_ratio, calmar_ratio, "
            "var_95, cvar_95, max_drawdown "
            "FROM risk_metrics WHERE ticker = ? "
            "ORDER BY computed_date DESC LIMIT 1",
            [ticker],
        ).fetchone()

        z20 = float(risk_row[0]) if risk_row and risk_row[0] else 0.0
        sharpe = float(risk_row[1]) if risk_row and risk_row[1] else 0.0
        sortino = float(risk_row[2]) if risk_row and risk_row[2] else 0.0
        calmar = float(risk_row[3]) if risk_row and risk_row[3] else 0.0
        var95 = float(risk_row[4]) if risk_row and risk_row[4] else 0.0
        cvar95 = float(risk_row[5]) if risk_row and risk_row[5] else 0.0
        mdd = float(risk_row[6]) if risk_row and risk_row[6] else 0.0

        # ── Fetch technicals (Bollinger) ───────────────────────────
        tech_row = db.execute(
            "SELECT bb_upper, bb_lower FROM technicals "
            "WHERE ticker = ? ORDER BY date DESC LIMIT 1",
            [ticker],
        ).fetchone()

        bb_upper = float(tech_row[0]) if tech_row and tech_row[0] else closes[-1]
        bb_lower = float(tech_row[1]) if tech_row and tech_row[1] else closes[-1]
        bb_bandwidth = (bb_upper - bb_lower) / closes[-1] if closes[-1] > 0 else 0.0

        # ── Fetch earnings proximity ───────────────────────────────
        earn_row = db.execute(
            "SELECT days_until_earnings FROM earnings_calendar "
            "WHERE ticker = ? ORDER BY snapshot_date DESC LIMIT 1",
            [ticker],
        ).fetchone()
        days_earn = int(earn_row[0]) if earn_row and earn_row[0] else None

        # ── Fetch insider activity ─────────────────────────────────
        insider_row = db.execute(
            "SELECT net_insider_buying_90d FROM insider_activity "
            "WHERE ticker = ? ORDER BY snapshot_date DESC LIMIT 1",
            [ticker],
        ).fetchone()
        net_insider = float(insider_row[0]) if insider_row and insider_row[0] else 0.0

        # ── Fetch fundamentals (for PhD equations + company profile) ─
        fund_row = db.execute(
            "SELECT trailing_pe, market_cap, revenue, net_income, "
            "return_on_assets, debt_to_equity, sector, industry "
            "FROM fundamentals WHERE ticker = ? "
            "ORDER BY snapshot_date DESC LIMIT 1",
            [ticker],
        ).fetchone()

        trailing_pe = float(fund_row[0]) if fund_row and fund_row[0] else 0.0
        market_cap = float(fund_row[1]) if fund_row and fund_row[1] else 0.0
        fund_revenue = float(fund_row[2]) if fund_row and fund_row[2] else 0.0
        fund_net_income = float(fund_row[3]) if fund_row and fund_row[3] else 0.0
        fund_roa = float(fund_row[4]) if fund_row and fund_row[4] else 0.0
        fund_sector = str(fund_row[6]) if fund_row and fund_row[6] else ""
        fund_industry = str(fund_row[7]) if fund_row and fund_row[7] else ""

        # ── Fetch balance sheet (for Altman Z + Piotroski) ─────────
        bs_rows = db.execute(
            "SELECT year, total_assets, total_liabilities, current_assets, "
            "current_liabilities, total_debt, current_ratio "
            "FROM balance_sheet WHERE ticker = ? "
            "ORDER BY year DESC LIMIT 2",
            [ticker],
        ).fetchall()

        # ── Fetch cash flows (for Piotroski) ───────────────────────
        cf_row = db.execute(
            "SELECT operating_cashflow FROM cash_flows "
            "WHERE ticker = ? ORDER BY year DESC LIMIT 1",
            [ticker],
        ).fetchone()
        operating_cf = float(cf_row[0]) if cf_row and cf_row[0] else 0.0

        # ── Fetch financial history (for Piotroski gross margin) ───
        fh_rows = db.execute(
            "SELECT year, gross_margin, revenue, net_income "
            "FROM financial_history WHERE ticker = ? "
            "ORDER BY year DESC LIMIT 2",
            [ticker],
        ).fetchall()

        # ── Compute original signals ──────────────────────────────
        r_z = robust_z_score(closes)
        bb_b = bollinger_pct_b(closes[-1], bb_upper, bb_lower)
        pctl_price = percentile_rank(closes, closes[-1])
        pctl_vol = percentile_rank(volumes, volumes[-1]) if volumes else 50.0
        om = omega_ratio(returns)
        wr, aw, al = _win_loss_stats(returns)
        kf = kelly_fraction(wr, aw, al)
        hk = kf / 2.0

        # ── Compute PhD-level signals ─────────────────────────────
        mom_12m = momentum_factor(closes)
        mr_score = mean_reversion_score(closes)
        hurst = hurst_exponent(closes)
        vwap_dev = vwap_deviation(closes, volumes)
        ey_gap = earnings_yield_gap(trailing_pe)

        # ── Altman Z-Score ──
        az = 0.0
        if bs_rows:
            bs_cur = bs_rows[0]
            ta = float(bs_cur[1]) if bs_cur[1] else 0.0
            tl = float(bs_cur[2]) if bs_cur[2] else 0.0
            ca = float(bs_cur[3]) if bs_cur[3] else 0.0
            cl = float(bs_cur[4]) if bs_cur[4] else 0.0

            if ta > 0:
                wc = ca - cl
                # Use operating_margin * revenue as EBIT proxy
                ebit_est = fund_revenue * fund_roa if fund_revenue > 0 else fund_net_income
                # Retained earnings proxy: stockholders_equity - paid-in capital
                # Approximate as net_income as a surrogate
                re_est = fund_net_income

                az = altman_z_score(
                    working_capital=wc,
                    retained_earnings=re_est,
                    ebit=ebit_est,
                    market_cap=market_cap,
                    total_liabilities=tl,
                    revenue=fund_revenue,
                    total_assets=ta,
                )

        # ── Piotroski F-Score ──
        pf = 0
        if bs_rows and fh_rows:
            bs_cur = bs_rows[0]
            bs_prev = bs_rows[1] if len(bs_rows) > 1 else bs_rows[0]
            fh_cur = fh_rows[0]
            fh_prev = fh_rows[1] if len(fh_rows) > 1 else fh_rows[0]

            ta_cur = float(bs_cur[1]) if bs_cur[1] else 1.0
            ta_prev = float(bs_prev[1]) if bs_prev[1] else 1.0
            debt_cur = float(bs_cur[5]) if bs_cur[5] else 0.0
            debt_prev = float(bs_prev[5]) if bs_prev[5] else 0.0
            cr_cur = float(bs_cur[6]) if bs_cur[6] else 0.0
            cr_prev = float(bs_prev[6]) if bs_prev[6] else 0.0

            gm_cur = float(fh_cur[1]) if fh_cur[1] else 0.0
            gm_prev = float(fh_prev[1]) if fh_prev[1] else 0.0
            rev_cur = float(fh_cur[2]) if fh_cur[2] else 0.0
            rev_prev = float(fh_prev[2]) if fh_prev[2] else 0.0

            roa_prev_est = (
                float(fh_prev[3]) / ta_prev if fh_prev[3] and ta_prev > 0 else 0.0
            )
            at_cur = rev_cur / ta_cur if ta_cur > 0 else 0.0
            at_prev = rev_prev / ta_prev if ta_prev > 0 else 0.0

            pf = piotroski_f_score(
                net_income=fund_net_income,
                operating_cf=operating_cf,
                roa_current=fund_roa,
                roa_previous=roa_prev_est,
                debt_current=debt_cur,
                debt_previous=debt_prev,
                current_ratio_current=cr_cur,
                current_ratio_previous=cr_prev,
                gross_margin_current=gm_cur,
                gross_margin_previous=gm_prev,
                asset_turnover_current=at_cur,
                asset_turnover_previous=at_prev,
            )

        # ── Fama-French Alpha (simplified) ──
        # α = R_stock - [R_f + β(R_m - R_f)]
        # We approximate using the stored beta and alpha from risk_metrics
        risk_alpha_row = db.execute(
            "SELECT alpha, beta FROM risk_metrics "
            "WHERE ticker = ? ORDER BY computed_date DESC LIMIT 1",
            [ticker],
        ).fetchone()
        ff_alpha = float(risk_alpha_row[0]) if risk_alpha_row and risk_alpha_row[0] else 0.0

        # ── Build scorecard ───────────────────────────────────────
        cap_tier = classify_cap_tier(market_cap)

        # Compute new scores
        rs_rating = min(99.0, pctl_price)  # Simple proxy for RS Rating (0-100)
        trend_score = compute_trend_template_score(
            current_price=closes[-1],
            sma_50=sma_50,
            sma_150=sma_150,
            sma_200=sma_200,
            high_52w=high_52w,
            low_52w=low_52w,
            rs_rating=rs_rating,
        )
        vcp_score = compute_vcp_score(
            natr=natr_14,
            volume_contraction=vol_contracting,
            bollinger_bandwidth=bb_bandwidth,
        )

        scorecard = QuantScorecard(
            ticker=ticker,
            computed_at=now,
            z_score_20d=z20,
            robust_z_score_20d=r_z,
            bollinger_pct_b=bb_b,
            percentile_rank_price=pctl_price,
            percentile_rank_volume=pctl_vol,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            calmar_ratio=calmar,
            omega_ratio=om,
            kelly_fraction=kf,
            half_kelly=hk,
            var_95=var95,
            cvar_95=cvar95,
            max_drawdown=mdd,
            # PhD-level signals
            momentum_12m=mom_12m,
            mean_reversion_score=mr_score,
            hurst_exponent=hurst,
            vwap_deviation=vwap_dev,
            fama_french_alpha=ff_alpha,
            earnings_yield_gap=ey_gap,
            altman_z_score=az,
            piotroski_f_score=pf,
            # Company classifiers
            sector=fund_sector,
            industry=fund_industry,
            market_cap=market_cap,
            market_cap_tier=cap_tier,
            # Minervini / O'Neil
            trend_template_score=trend_score,
            vcp_setup_score=vcp_score,
            relative_strength_rating=rs_rating,
        )

        # Anomaly detection
        scorecard.flags = generate_flags(scorecard, days_earn, net_insider)

        # ── Quality / Junk Detection ──────────────────────────────
        current_price = closes[-1]
        avg_vol = float(np.mean(volumes[-50:])) if len(volumes) >= 50 else 0.0

        if current_price < 2.0:
            scorecard.flags.append("penny_stock")
        if 0 < market_cap < 50_000_000:
            scorecard.flags.append("micro_junk")
        if natr_14 > 10.0 and curr_vol > avg_vol_50 * 5:
            scorecard.flags.append("pump_dump")
        if avg_vol < 50_000:
            scorecard.flags.append("illiquid")

        # ── Persist ────────────────────────────────────────────────
        self._store(db, scorecard)

        logger.info(
            "[Quant] %s: Trend=%.0f VCP=%.0f RS=%.0f cap=%s Z=%.2f Mom=%.2f "
            "Hurst=%.2f AltZ=%.2f Piof=%d flags=%s",
            ticker, trend_score, vcp_score, rs_rating, cap_tier, z20, mom_12m,
            hurst, az, pf, scorecard.flags,
        )
        return scorecard

    # -------------------------------------------------------------------
    @staticmethod
    def _store(db, sc: QuantScorecard) -> None:  # noqa: ANN001
        """Persist a scorecard row in DuckDB."""
        sc_id = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO quant_scorecards
                (id, ticker, computed_at,
                 z_score_20d, robust_z_score, bollinger_pct_b,
                 pctl_rank_price, pctl_rank_volume,
                 sharpe_ratio, sortino_ratio, calmar_ratio,
                 omega_ratio, kelly_fraction, half_kelly,
                 var_95, cvar_95, max_drawdown,
                 momentum_12m, mean_reversion_score, hurst_exponent,
                 vwap_deviation, fama_french_alpha, earnings_yield_gap,
                 altman_z_score, piotroski_f_score,
                 flags, trend_template_score, vcp_setup_score, rs_rating)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                sc_id,
                sc.ticker,
                sc.computed_at,
                sc.z_score_20d,
                sc.robust_z_score_20d,
                sc.bollinger_pct_b,
                sc.percentile_rank_price,
                sc.percentile_rank_volume,
                sc.sharpe_ratio,
                sc.sortino_ratio,
                sc.calmar_ratio,
                sc.omega_ratio,
                sc.kelly_fraction,
                sc.half_kelly,
                sc.var_95,
                sc.cvar_95,
                sc.max_drawdown,
                sc.momentum_12m,
                sc.mean_reversion_score,
                sc.hurst_exponent,
                sc.vwap_deviation,
                sc.fama_french_alpha,
                sc.earnings_yield_gap,
                sc.altman_z_score,
                sc.piotroski_f_score,
                json.dumps(sc.flags),
                sc.trend_template_score,
                sc.vcp_setup_score,
                sc.relative_strength_rating,
            ],
        )
        db.commit()
