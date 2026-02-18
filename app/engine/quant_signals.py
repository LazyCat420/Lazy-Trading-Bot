"""Layer 1 — Quant Signal Engine.

Pure math, zero LLM calls.  Reads stored Phase-1 data from DuckDB and
produces a QuantScorecard for each ticker.

Reuses metrics already computed by RiskComputer (Sharpe, Sortino, Calmar,
VaR, CVaR, MaxDrawdown, Z-Score) and adds five new functions:
  • Robust Z-Score (MAD-based)
  • Bollinger %B
  • Percentile Rank
  • Omega Ratio
  • Kelly Criterion
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
# Pure quant helper functions
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
            "SELECT close, volume FROM price_history "
            "WHERE ticker = ? ORDER BY date ASC",
            [ticker],
        ).fetchall()

        if len(rows) < 20:
            logger.warning("[Quant] %s has only %d rows — too few", ticker, len(rows))
            return QuantScorecard(ticker=ticker, computed_at=now, flags=["insufficient_data"])

        closes = [float(r[0]) for r in rows]
        volumes = [float(r[1]) for r in rows if r[1] is not None]
        returns = np.diff(np.log(np.array(closes)))  # log returns

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

        # ── Compute new signals ────────────────────────────────────
        r_z = robust_z_score(closes)
        bb_b = bollinger_pct_b(closes[-1], bb_upper, bb_lower)
        pctl_price = percentile_rank(closes, closes[-1])
        pctl_vol = percentile_rank(volumes, volumes[-1]) if volumes else 50.0
        om = omega_ratio(returns)
        wr, aw, al = _win_loss_stats(returns)
        kf = kelly_fraction(wr, aw, al)
        hk = kf / 2.0

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
        )

        # Anomaly detection
        scorecard.flags = generate_flags(scorecard, days_earn, net_insider)

        # ── Persist ────────────────────────────────────────────────
        self._store(db, scorecard)

        logger.info(
            "[Quant] %s scorecard: Z=%.2f, RobZ=%.2f, %%B=%.2f, Omega=%.2f, "
            "Kelly=%.2f, flags=%s",
            ticker,
            z20,
            r_z,
            bb_b,
            om,
            kf,
            scorecard.flags,
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
                 var_95, cvar_95, max_drawdown, flags)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                json.dumps(sc.flags),
            ],
        )
        db.commit()
