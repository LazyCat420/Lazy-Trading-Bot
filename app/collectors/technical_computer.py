"""Technical indicator computation — comprehensive pandas-ta analysis.

Computes ALL 154 pandas-ta indicators using the 'All' strategy, storing:
- Key named columns in DuckDB for fast agent queries
- Full indicator JSON blob per day for complete data access
"""

from __future__ import annotations

import json

import pandas as pd
import pandas_ta as ta

from app.database import get_db
from app.models.market_data import TechnicalRow
from app.utils.logger import logger


class TechnicalComputer:
    """Computes technical indicators from stored price history.

    Uses pandas-ta Strategy("all") to compute ALL available indicators,
    then extracts key columns for structured DB storage.
    """

    # Key indicators to store as named columns (for agent queries)
    KEY_INDICATORS = [
        # Original
        "rsi", "macd", "macd_signal", "macd_hist",
        "sma_20", "sma_50", "sma_200",
        "bb_upper", "bb_middle", "bb_lower",
        "atr", "stoch_k", "stoch_d",
        # EMAs
        "ema_9", "ema_21", "ema_50", "ema_200",
        # Momentum
        "cci", "willr", "mfi", "roc", "mom",
        "ao", "tsi", "uo", "stochrsi_k",
        # Trend
        "adx", "adx_dmp", "adx_dmn",
        "aroon_up", "aroon_down", "aroon_osc",
        "supertrend", "psar",
        "chop", "vortex_pos", "vortex_neg",
        # Volatility
        "natr", "true_range",
        "donchian_upper", "donchian_lower", "donchian_mid",
        "kc_upper", "kc_lower",
        # Volume
        "obv", "ad", "cmf", "efi", "mfi", "pvt",
        # Statistics
        "zscore", "skew", "kurtosis", "entropy",
        # Ichimoku
        "ichi_conv", "ichi_base", "ichi_span_a", "ichi_span_b",
        # Fibonacci (computed separately)
        "fib_0", "fib_236", "fib_382", "fib_500", "fib_618", "fib_786", "fib_1",
    ]

    async def compute(self, ticker: str) -> list[TechnicalRow]:
        """Read price_history from DuckDB, compute ALL indicators, store results.

        Returns list of TechnicalRow objects with key indicators populated.
        """
        logger.info("Computing comprehensive technicals for %s", ticker)

        db = get_db()
        raw = db.execute(
            """
            SELECT date, open, high, low, close, volume
            FROM price_history
            WHERE ticker = ?
            ORDER BY date ASC
            """,
            [ticker],
        ).fetchall()

        if not raw or len(raw) < 30:
            logger.warning(
                "Not enough price data for %s (%d rows), need >= 30",
                ticker,
                len(raw) if raw else 0,
            )
            return []

        cols = ["date", "open", "high", "low", "close", "volume"]
        df = pd.DataFrame(raw, columns=cols)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()

        # ================================================================
        # Run ALL pandas-ta indicators via the "All" study
        # ================================================================
        logger.info(
            "Running pandas-ta AllStudy on %d rows for %s",
            len(df), ticker,
        )

        try:
            df.ta.study(ta.AllStudy)
        except Exception as e:
            logger.warning("AllStudy failed, falling back to individual: %s", e)
            # Fallback: compute key indicators individually (resilient)
            self._compute_individual(df)

        total_cols = len(df.columns) - 4  # minus OHLCV
        logger.info(
            "pandas-ta computed %d indicator columns for %s", total_cols, ticker,
        )

        # ================================================================
        # Compute Fibonacci retracement levels (not in pandas-ta)
        # ================================================================
        fib_levels = self._compute_fibonacci(df)

        # ================================================================
        # Extract key indicators + build full JSON per row
        # ================================================================
        col_map = self._build_column_map(df)

        rows: list[TechnicalRow] = []
        for i, idx in enumerate(df.index):
            dt = idx.date() if hasattr(idx, "date") else idx

            # Extract all non-OHLCV columns as a dict for this row
            all_indicators = {}
            for col in df.columns:
                if col in ("open", "high", "low", "close", "volume"):
                    continue
                val = df[col].iloc[i]
                if pd.notna(val):
                    all_indicators[col] = round(float(val), 6)

            # Add Fibonacci levels (same for all rows — based on lookback)
            all_indicators.update(fib_levels)

            # Build TechnicalRow with key named fields
            row = TechnicalRow(
                ticker=ticker,
                date=dt,
                # Original indicators
                rsi=self._get(col_map, df, i, "RSI_14"),
                macd=self._get(col_map, df, i, "MACD_12_26_9"),
                macd_signal=self._get(col_map, df, i, "MACDs_12_26_9"),
                macd_hist=self._get(col_map, df, i, "MACDh_12_26_9"),
                sma_20=self._get(col_map, df, i, "SMA_20"),
                sma_50=self._get(col_map, df, i, "SMA_50"),
                sma_200=self._get(col_map, df, i, "SMA_200"),
                bb_upper=self._get(col_map, df, i, "BBU_20_2.0"),
                bb_middle=self._get(col_map, df, i, "BBM_20_2.0"),
                bb_lower=self._get(col_map, df, i, "BBL_20_2.0"),
                atr=self._get(col_map, df, i, "ATRr_14"),
                stoch_k=self._get(col_map, df, i, "STOCHk_14_3_3"),
                stoch_d=self._get(col_map, df, i, "STOCHd_14_3_3"),
                # EMAs
                ema_9=self._get(col_map, df, i, "EMA_9"),
                ema_21=self._get(col_map, df, i, "EMA_21"),
                ema_50=self._get(col_map, df, i, "EMA_50"),
                ema_200=self._get(col_map, df, i, "EMA_200"),
                # Momentum
                cci=self._get(col_map, df, i, "CCI_14_0.015"),
                willr=self._get(col_map, df, i, "WILLR_14"),
                mfi=self._get(col_map, df, i, "MFI_14"),
                roc=self._get(col_map, df, i, "ROC_10"),
                mom=self._get(col_map, df, i, "MOM_10"),
                ao=self._get(col_map, df, i, "AO_5_34"),
                tsi=self._get(col_map, df, i, "TSI_13_25_13"),
                uo=self._get(col_map, df, i, "UO_7_14_28"),
                stochrsi_k=self._get(col_map, df, i, "STOCHRSIk_14_14_3_3"),
                # Trend
                adx=self._get(col_map, df, i, "ADX_14"),
                adx_dmp=self._get(col_map, df, i, "DMP_14"),
                adx_dmn=self._get(col_map, df, i, "DMN_14"),
                aroon_up=self._get(col_map, df, i, "AROONU_14"),
                aroon_down=self._get(col_map, df, i, "AROOND_14"),
                aroon_osc=self._get(col_map, df, i, "AROONOSC_14"),
                supertrend=self._get(col_map, df, i, "SUPERT_7_3.0"),
                psar=self._get(col_map, df, i, "PSARl_0.02_0.2"),
                chop=self._get(col_map, df, i, "CHOP_14_1_100.0"),
                vortex_pos=self._get(col_map, df, i, "VTXP_14"),
                vortex_neg=self._get(col_map, df, i, "VTXM_14"),
                # Volatility
                natr=self._get(col_map, df, i, "NATR_14"),
                true_range=self._get(col_map, df, i, "TRUERANGE_1"),
                donchian_upper=self._get(col_map, df, i, "DCU_20_20"),
                donchian_lower=self._get(col_map, df, i, "DCL_20_20"),
                donchian_mid=self._get(col_map, df, i, "DCM_20_20"),
                kc_upper=self._get(col_map, df, i, "KCUe_20_2"),
                kc_lower=self._get(col_map, df, i, "KCLe_20_2"),
                # Volume
                obv=self._get(col_map, df, i, "OBV"),
                ad=self._get(col_map, df, i, "AD"),
                cmf=self._get(col_map, df, i, "CMF_20"),
                efi=self._get(col_map, df, i, "EFI_13"),
                pvt=self._get(col_map, df, i, "PVT"),
                # Statistics
                zscore=self._get(col_map, df, i, "ZS_30"),
                skew=self._get(col_map, df, i, "SKEW_30"),
                kurtosis=self._get(col_map, df, i, "KURT_30"),
                entropy=self._get(col_map, df, i, "ENTP_10"),
                # Ichimoku
                ichi_conv=self._get(col_map, df, i, "ITS_9"),
                ichi_base=self._get(col_map, df, i, "IKS_26"),
                ichi_span_a=self._get(col_map, df, i, "ISA_9"),
                ichi_span_b=self._get(col_map, df, i, "ISB_26"),
                # Fibonacci
                fib_0=fib_levels.get("fib_0"),
                fib_236=fib_levels.get("fib_236"),
                fib_382=fib_levels.get("fib_382"),
                fib_500=fib_levels.get("fib_500"),
                fib_618=fib_levels.get("fib_618"),
                fib_786=fib_levels.get("fib_786"),
                fib_1=fib_levels.get("fib_1"),
                # Full JSON blob of ALL indicators
                all_indicators_json=json.dumps(all_indicators),
            )
            rows.append(row)

        # ================================================================
        # Persist to DB
        # ================================================================
        self._persist(db, rows)
        logger.info("Stored %d comprehensive technical rows for %s", len(rows), ticker)
        return rows

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _build_column_map(df: pd.DataFrame) -> dict[str, str]:
        """Build a case-insensitive lookup map for DataFrame column names.

        pandas-ta column names vary between versions (e.g. ATR_14 vs ATRr_14).
        This lets us find columns by fuzzy prefix match.
        """
        col_map: dict[str, str] = {}
        for col in df.columns:
            # Store lowercase version for lookup
            col_map[col.upper()] = col
            col_map[col] = col
        return col_map

    @staticmethod
    def _get(
        col_map: dict[str, str],
        df: pd.DataFrame,
        row_idx: int,
        col_name: str,
    ) -> float | None:
        """Safely extract a value from the DataFrame using flexible column matching."""
        # Try exact match first
        actual = col_map.get(col_name) or col_map.get(col_name.upper())

        # Try prefix match if exact fails
        if actual is None:
            prefix = col_name.split("_")[0]
            for key, val in col_map.items():
                if key.upper().startswith(prefix.upper()) and key not in (
                    "open", "high", "low", "close", "volume",
                ):
                    actual = val
                    break

        if actual is None or actual not in df.columns:
            return None

        val = df[actual].iloc[row_idx]
        if pd.isna(val):
            return None
        return round(float(val), 4)

    @staticmethod
    def _compute_fibonacci(df: pd.DataFrame, lookback: int = 120) -> dict[str, float]:
        """Compute Fibonacci retracement levels from recent swing high/low."""
        n = min(lookback, len(df))
        recent = df.tail(n)
        swing_high = float(recent["high"].max())
        swing_low = float(recent["low"].min())
        diff = swing_high - swing_low

        if diff == 0:
            return {}

        return {
            "fib_0": round(swing_high, 4),
            "fib_236": round(swing_high - diff * 0.236, 4),
            "fib_382": round(swing_high - diff * 0.382, 4),
            "fib_500": round(swing_high - diff * 0.500, 4),
            "fib_618": round(swing_high - diff * 0.618, 4),
            "fib_786": round(swing_high - diff * 0.786, 4),
            "fib_1": round(swing_low, 4),
        }

    @staticmethod
    def _compute_individual(df: pd.DataFrame) -> None:
        """Fallback: compute key indicators individually if study fails.

        Each indicator is wrapped in its own try/except so one failure
        doesn't prevent the rest from computing.
        """
        indicators = [
            # (method_name, kwargs)
            ("rsi", {"length": 14}),
            ("macd", {"fast": 12, "slow": 26, "signal": 9}),
            ("sma", {"length": 20}),
            ("sma", {"length": 50}),
            ("sma", {"length": 200}),
            ("ema", {"length": 9}),
            ("ema", {"length": 21}),
            ("ema", {"length": 50}),
            ("ema", {"length": 200}),
            ("bbands", {"length": 20, "std": 2}),
            ("atr", {"length": 14}),
            ("stoch", {"k": 14, "d": 3, "smooth_k": 3}),
            ("adx", {"length": 14}),
            ("aroon", {"length": 14}),
            ("cci", {"length": 14}),
            ("willr", {"length": 14}),
            ("mfi", {"length": 14}),
            ("roc", {"length": 10}),
            ("mom", {"length": 10}),
            ("obv", {}),
            ("cmf", {"length": 20}),
            ("psar", {}),
            ("supertrend", {}),
            ("natr", {"length": 14}),
            ("donchian", {"lower_length": 20, "upper_length": 20}),
            ("kc", {"length": 20, "scalar": 2}),
            ("tsi", {}),
            ("uo", {}),
            ("stochrsi", {}),
            ("ichimoku", {}),
            ("chop", {"length": 14}),
            ("vortex", {"length": 14}),
            ("ao", {}),
            ("zscore", {"length": 30}),
            ("skew", {"length": 30}),
            ("kurtosis", {"length": 30}),
            ("entropy", {"length": 10}),
            ("efi", {"length": 13}),
            ("pvt", {}),
            ("ad", {}),
            ("true_range", {}),
        ]

        computed = 0
        failed = 0
        for method_name, kwargs in indicators:
            try:
                fn = getattr(df.ta, method_name)
                fn(append=True, **kwargs)
                computed += 1
            except Exception as e:
                failed += 1
                logger.debug(
                    "Individual indicator %s failed: %s", method_name, e
                )

        logger.info(
            "Individual fallback: %d computed, %d failed", computed, failed
        )

    @staticmethod
    def _persist(db, rows: list[TechnicalRow]) -> None:
        """Store all technical rows in DuckDB."""
        for r in rows:
            db.execute(
                """
                INSERT OR REPLACE INTO technicals
                    (ticker, date, rsi, macd, macd_signal, macd_hist,
                     sma_20, sma_50, sma_200, bb_upper, bb_middle, bb_lower,
                     atr, stoch_k, stoch_d,
                     ema_9, ema_21, ema_50, ema_200,
                     cci, willr, mfi, roc, mom, ao, tsi, uo, stochrsi_k,
                     adx, adx_dmp, adx_dmn,
                     aroon_up, aroon_down, aroon_osc,
                     supertrend, psar, chop, vortex_pos, vortex_neg,
                     natr, true_range,
                     donchian_upper, donchian_lower, donchian_mid,
                     kc_upper, kc_lower,
                     obv, ad, cmf, efi, pvt,
                     zscore, skew, kurtosis, entropy,
                     ichi_conv, ichi_base, ichi_span_a, ichi_span_b,
                     fib_0, fib_236, fib_382, fib_500, fib_618, fib_786, fib_1,
                     all_indicators_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?, ?, ?,
                        ?, ?,
                        ?, ?, ?,
                        ?, ?,
                        ?, ?, ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?,
                        ?)
                """,
                [
                    r.ticker, r.date, r.rsi, r.macd, r.macd_signal, r.macd_hist,
                    r.sma_20, r.sma_50, r.sma_200, r.bb_upper, r.bb_middle, r.bb_lower,
                    r.atr, r.stoch_k, r.stoch_d,
                    r.ema_9, r.ema_21, r.ema_50, r.ema_200,
                    r.cci, r.willr, r.mfi, r.roc, r.mom, r.ao, r.tsi, r.uo, r.stochrsi_k,
                    r.adx, r.adx_dmp, r.adx_dmn,
                    r.aroon_up, r.aroon_down, r.aroon_osc,
                    r.supertrend, r.psar, r.chop, r.vortex_pos, r.vortex_neg,
                    r.natr, r.true_range,
                    r.donchian_upper, r.donchian_lower, r.donchian_mid,
                    r.kc_upper, r.kc_lower,
                    r.obv, r.ad, r.cmf, r.efi, r.pvt,
                    r.zscore, r.skew, r.kurtosis, r.entropy,
                    r.ichi_conv, r.ichi_base, r.ichi_span_a, r.ichi_span_b,
                    r.fib_0, r.fib_236, r.fib_382, r.fib_500, r.fib_618, r.fib_786, r.fib_1,
                    r.all_indicators_json,
                ],
            )
