"""DuckDB session management and table initialization."""

from __future__ import annotations

import duckdb

from app.config import settings
from app.utils.logger import logger

_connection: duckdb.DuckDBPyConnection | None = None


def get_db() -> duckdb.DuckDBPyConnection:
    """Return the singleton DuckDB connection, creating tables on first call."""
    global _connection
    if _connection is None:
        db_path = str(settings.DB_PATH)
        logger.info("Opening DuckDB at %s", db_path)
        _connection = duckdb.connect(db_path)
        _init_tables(_connection)
    return _connection


def _init_tables(conn: duckdb.DuckDBPyConnection) -> None:
    """Create tables if they don't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_history (
            ticker     VARCHAR NOT NULL,
            date       DATE NOT NULL,
            open       DOUBLE,
            high       DOUBLE,
            low        DOUBLE,
            close      DOUBLE,
            volume     BIGINT,
            adj_close  DOUBLE,
            PRIMARY KEY (ticker, date)
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS fundamentals (
            ticker              VARCHAR NOT NULL,
            snapshot_date       DATE NOT NULL,
            market_cap          DOUBLE,
            trailing_pe         DOUBLE,
            forward_pe          DOUBLE,
            peg_ratio           DOUBLE,
            price_to_sales      DOUBLE,
            price_to_book       DOUBLE,
            enterprise_value    DOUBLE,
            ev_to_revenue       DOUBLE,
            ev_to_ebitda        DOUBLE,
            profit_margin       DOUBLE,
            operating_margin    DOUBLE,
            return_on_assets    DOUBLE,
            return_on_equity    DOUBLE,
            revenue             DOUBLE,
            revenue_growth      DOUBLE,
            net_income          DOUBLE,
            trailing_eps        DOUBLE,
            total_cash          DOUBLE,
            total_debt          DOUBLE,
            debt_to_equity      DOUBLE,
            free_cash_flow      DOUBLE,
            dividend_rate       DOUBLE,
            dividend_yield      DOUBLE,
            payout_ratio        DOUBLE,
            sector              VARCHAR,
            industry            VARCHAR,
            description         VARCHAR,
            raw_json            VARCHAR,
            PRIMARY KEY (ticker, snapshot_date)
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS financial_history (
            ticker           VARCHAR NOT NULL,
            year             INTEGER NOT NULL,
            revenue          DOUBLE,
            net_income       DOUBLE,
            gross_margin     DOUBLE,
            operating_margin DOUBLE,
            net_margin       DOUBLE,
            eps              DOUBLE,
            PRIMARY KEY (ticker, year)
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS technicals (
            ticker          VARCHAR NOT NULL,
            date            DATE NOT NULL,
            -- Core (original)
            rsi             DOUBLE,
            macd            DOUBLE,
            macd_signal     DOUBLE,
            macd_hist       DOUBLE,
            sma_20          DOUBLE,
            sma_50          DOUBLE,
            sma_200         DOUBLE,
            bb_upper        DOUBLE,
            bb_middle       DOUBLE,
            bb_lower        DOUBLE,
            atr             DOUBLE,
            stoch_k         DOUBLE,
            stoch_d         DOUBLE,
            -- EMAs
            ema_9           DOUBLE,
            ema_21          DOUBLE,
            ema_50          DOUBLE,
            ema_200         DOUBLE,
            -- Momentum
            cci             DOUBLE,
            willr           DOUBLE,
            mfi             DOUBLE,
            roc             DOUBLE,
            mom             DOUBLE,
            ao              DOUBLE,
            tsi             DOUBLE,
            uo              DOUBLE,
            stochrsi_k      DOUBLE,
            -- Trend
            adx             DOUBLE,
            adx_dmp         DOUBLE,
            adx_dmn         DOUBLE,
            aroon_up        DOUBLE,
            aroon_down      DOUBLE,
            aroon_osc       DOUBLE,
            supertrend      DOUBLE,
            psar            DOUBLE,
            chop            DOUBLE,
            vortex_pos      DOUBLE,
            vortex_neg      DOUBLE,
            -- Volatility
            natr            DOUBLE,
            true_range      DOUBLE,
            donchian_upper  DOUBLE,
            donchian_lower  DOUBLE,
            donchian_mid    DOUBLE,
            kc_upper        DOUBLE,
            kc_lower        DOUBLE,
            -- Volume
            obv             DOUBLE,
            ad              DOUBLE,
            cmf             DOUBLE,
            efi             DOUBLE,
            pvt             DOUBLE,
            -- Statistics
            zscore          DOUBLE,
            skew            DOUBLE,
            kurtosis        DOUBLE,
            entropy         DOUBLE,
            -- Ichimoku
            ichi_conv       DOUBLE,
            ichi_base       DOUBLE,
            ichi_span_a     DOUBLE,
            ichi_span_b     DOUBLE,
            -- Fibonacci
            fib_0           DOUBLE,
            fib_236         DOUBLE,
            fib_382         DOUBLE,
            fib_500         DOUBLE,
            fib_618         DOUBLE,
            fib_786         DOUBLE,
            fib_1           DOUBLE,
            -- Full JSON of all 154 indicator columns
            all_indicators_json VARCHAR,
            PRIMARY KEY (ticker, date)
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS news_articles (
            ticker        VARCHAR NOT NULL,
            article_hash  VARCHAR NOT NULL,
            title         VARCHAR,
            publisher     VARCHAR,
            url           VARCHAR,
            published_at  TIMESTAMP,
            summary       VARCHAR,
            thumbnail_url VARCHAR,
            source        VARCHAR DEFAULT 'yfinance',
            collected_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (ticker, article_hash)
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS youtube_transcripts (
            ticker           VARCHAR NOT NULL,
            video_id         VARCHAR NOT NULL,
            title            VARCHAR,
            channel          VARCHAR,
            published_at     TIMESTAMP,
            duration_seconds INTEGER,
            raw_transcript   VARCHAR,
            collected_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            scanned_for_tickers BOOLEAN DEFAULT FALSE,
            PRIMARY KEY (ticker, video_id)
        );
    """)

    # ---- Phase 8: Expanded tables ----

    conn.execute("""
        CREATE TABLE IF NOT EXISTS risk_metrics (
            ticker                    VARCHAR NOT NULL,
            computed_date             DATE NOT NULL,
            z_score_20                DOUBLE,
            z_score_50                DOUBLE,
            sharpe_ratio              DOUBLE,
            sortino_ratio             DOUBLE,
            calmar_ratio              DOUBLE,
            treynor_ratio             DOUBLE,
            var_95                    DOUBLE,
            var_99                    DOUBLE,
            cvar_95                   DOUBLE,
            cvar_99                   DOUBLE,
            max_drawdown              DOUBLE,
            max_drawdown_duration_days INTEGER,
            current_drawdown          DOUBLE,
            daily_volatility          DOUBLE,
            annualized_volatility     DOUBLE,
            downside_deviation        DOUBLE,
            volatility_skew           DOUBLE,
            return_kurtosis           DOUBLE,
            beta                      DOUBLE,
            alpha                     DOUBLE,
            r_squared                 DOUBLE,
            correlation_to_spy        DOUBLE,
            gain_to_pain_ratio        DOUBLE,
            tail_ratio                DOUBLE,
            ulcer_index               DOUBLE,
            information_ratio         DOUBLE,
            PRIMARY KEY (ticker, computed_date)
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS balance_sheet (
            ticker              VARCHAR NOT NULL,
            year                INTEGER NOT NULL,
            total_assets        DOUBLE,
            total_liabilities   DOUBLE,
            stockholders_equity DOUBLE,
            current_assets      DOUBLE,
            current_liabilities DOUBLE,
            current_ratio       DOUBLE,
            total_debt          DOUBLE,
            cash_and_equivalents DOUBLE,
            net_working_capital DOUBLE,
            goodwill            DOUBLE,
            tangible_book_value DOUBLE,
            PRIMARY KEY (ticker, year)
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS cash_flows (
            ticker               VARCHAR NOT NULL,
            year                 INTEGER NOT NULL,
            operating_cashflow   DOUBLE,
            capital_expenditures DOUBLE,
            free_cashflow        DOUBLE,
            financing_cashflow   DOUBLE,
            investing_cashflow   DOUBLE,
            dividends_paid       DOUBLE,
            share_buybacks       DOUBLE,
            net_change_in_cash   DOUBLE,
            PRIMARY KEY (ticker, year)
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS analyst_data (
            ticker        VARCHAR NOT NULL,
            snapshot_date  DATE NOT NULL,
            target_mean   DOUBLE,
            target_median DOUBLE,
            target_high   DOUBLE,
            target_low    DOUBLE,
            num_analysts  INTEGER,
            strong_buy    INTEGER,
            buy           INTEGER,
            hold          INTEGER,
            sell          INTEGER,
            strong_sell   INTEGER,
            PRIMARY KEY (ticker, snapshot_date)
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS insider_activity (
            ticker                    VARCHAR NOT NULL,
            snapshot_date             DATE NOT NULL,
            net_insider_buying_90d    DOUBLE,
            institutional_ownership_pct DOUBLE,
            raw_transactions          VARCHAR,
            PRIMARY KEY (ticker, snapshot_date)
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS earnings_calendar (
            ticker             VARCHAR NOT NULL,
            snapshot_date      DATE NOT NULL,
            next_earnings_date DATE,
            days_until_earnings INTEGER,
            earnings_estimate  DOUBLE,
            previous_actual    DOUBLE,
            previous_estimate  DOUBLE,
            surprise_pct       DOUBLE,
            PRIMARY KEY (ticker, snapshot_date)
        );
    """)

    # ---- Phase 12: Ticker Discovery tables ----

    conn.execute("""
        CREATE TABLE IF NOT EXISTS discovered_tickers (
            ticker          VARCHAR NOT NULL,
            source          VARCHAR NOT NULL,
            source_detail   VARCHAR DEFAULT '',
            discovery_score DOUBLE DEFAULT 0.0,
            sentiment_hint  VARCHAR DEFAULT 'neutral',
            context_snippet VARCHAR DEFAULT '',
            source_url      VARCHAR DEFAULT '',
            discovered_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ticker_scores (
            ticker          VARCHAR PRIMARY KEY,
            total_score     DOUBLE DEFAULT 0.0,
            youtube_score   DOUBLE DEFAULT 0.0,
            reddit_score    DOUBLE DEFAULT 0.0,
            mention_count   INTEGER DEFAULT 0,
            first_seen      TIMESTAMP,
            last_seen       TIMESTAMP,
            sentiment_hint  VARCHAR DEFAULT 'neutral',
            is_validated    BOOLEAN DEFAULT FALSE,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # ---- Phase 2: Watchlist table (bridges Discovery → Analysis) ----

    conn.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            ticker          VARCHAR NOT NULL,
            source          VARCHAR DEFAULT 'manual',
            added_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_analyzed   TIMESTAMP,
            analysis_count  INTEGER DEFAULT 0,
            signal          VARCHAR DEFAULT 'PENDING',
            confidence      DOUBLE DEFAULT 0.0,
            discovery_score DOUBLE DEFAULT 0.0,
            sentiment_hint  VARCHAR DEFAULT 'neutral',
            status          VARCHAR DEFAULT 'active',
            cooldown_until  TIMESTAMP,
            notes           VARCHAR DEFAULT '',
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            bot_id          VARCHAR DEFAULT 'default',
            PRIMARY KEY (ticker, bot_id)
        );
    """)

    # ---- Phase 2: Deep Analysis tables ----

    conn.execute("""
        CREATE TABLE IF NOT EXISTS quant_scorecards (
            id                VARCHAR PRIMARY KEY,
            ticker            VARCHAR NOT NULL,
            computed_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            z_score_20d       DOUBLE,
            robust_z_score    DOUBLE,
            bollinger_pct_b   DOUBLE,
            pctl_rank_price   DOUBLE,
            pctl_rank_volume  DOUBLE,
            sharpe_ratio      DOUBLE,
            sortino_ratio     DOUBLE,
            calmar_ratio      DOUBLE,
            omega_ratio       DOUBLE,
            kelly_fraction    DOUBLE,
            half_kelly        DOUBLE,
            var_95            DOUBLE,
            cvar_95           DOUBLE,
            max_drawdown      DOUBLE,
            flags             VARCHAR DEFAULT '[]'
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ticker_dossiers (
            id                VARCHAR PRIMARY KEY,
            ticker            VARCHAR NOT NULL,
            generated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            version           INTEGER DEFAULT 1,
            scorecard_json    VARCHAR,
            qa_pairs_json     VARCHAR,
            executive_summary VARCHAR,
            bull_case         VARCHAR,
            bear_case         VARCHAR,
            key_catalysts     VARCHAR DEFAULT '[]',
            conviction_score  DOUBLE DEFAULT 0.5,
            total_tokens      INTEGER DEFAULT 0
        );
    """)

    # ── Phase 3: Trading Engine tables ─────────────────────────
    # ── Positions: migrate old ticker-only PK → composite (ticker, bot_id) ──
    # NOTE: The old check ("bot_id" not in cols) was wrong — _migrate_columns()
    # adds bot_id via ALTER TABLE but never fixes the PK. We now check the
    # actual PK column count to detect the stale single-column PK.
    try:
        pk_cols = conn.execute(
            "SELECT column_name FROM information_schema.key_column_usage "
            "WHERE table_name = 'positions'"
        ).fetchall()
        needs_pk_migration = len(pk_cols) == 1  # Only 'ticker', missing 'bot_id' in PK
        if needs_pk_migration:
            logger.info(
                "[DB] Detected single-column PK on positions (cols=%s) → "
                "rebuilding with composite PK (ticker, bot_id)",
                [r[0] for r in pk_cols],
            )
            conn.execute("ALTER TABLE positions RENAME TO _positions_old")
            conn.execute("""
                CREATE TABLE positions (
                    ticker            VARCHAR NOT NULL,
                    qty               INTEGER NOT NULL,
                    avg_entry_price   DOUBLE NOT NULL,
                    stop_loss         DOUBLE DEFAULT 0,
                    take_profit       DOUBLE DEFAULT 0,
                    trailing_stop_pct DOUBLE DEFAULT 0,
                    opened_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_updated      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    bot_id            VARCHAR NOT NULL DEFAULT 'default',
                    PRIMARY KEY (ticker, bot_id)
                );
            """)
            # Migrate data — handle both old (no bot_id) and new (has bot_id) schemas
            old_cols = [r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = '_positions_old'"
            ).fetchall()]
            if "bot_id" in old_cols:
                conn.execute("""
                    INSERT INTO positions
                        (ticker, qty, avg_entry_price, stop_loss, take_profit,
                         trailing_stop_pct, opened_at, last_updated, bot_id)
                    SELECT ticker, qty, avg_entry_price,
                           COALESCE(stop_loss, 0), COALESCE(take_profit, 0),
                           COALESCE(trailing_stop_pct, 0), opened_at, last_updated,
                           COALESCE(bot_id, 'default')
                    FROM _positions_old
                """)
            else:
                conn.execute("""
                    INSERT INTO positions
                        (ticker, qty, avg_entry_price, stop_loss, take_profit,
                         trailing_stop_pct, opened_at, last_updated, bot_id)
                    SELECT ticker, qty, avg_entry_price,
                           COALESCE(stop_loss, 0), COALESCE(take_profit, 0),
                           COALESCE(trailing_stop_pct, 0), opened_at, last_updated,
                           'default'
                    FROM _positions_old
                """)
            conn.execute("DROP TABLE _positions_old")
            conn.commit()
            logger.info("[DB] Positions PK migration complete → (ticker, bot_id)")
    except Exception as exc:
        # Table doesn't exist yet — CREATE below handles it.
        # Log the error so it's not silently swallowed.
        logger.debug("[DB] Positions migration skipped: %s", exc)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            ticker            VARCHAR NOT NULL,
            qty               INTEGER NOT NULL,
            avg_entry_price   DOUBLE NOT NULL,
            stop_loss         DOUBLE DEFAULT 0,
            take_profit       DOUBLE DEFAULT 0,
            trailing_stop_pct DOUBLE DEFAULT 0,
            opened_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_updated      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            bot_id            VARCHAR NOT NULL DEFAULT 'default',
            PRIMARY KEY (ticker, bot_id)
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id               VARCHAR PRIMARY KEY,
            ticker           VARCHAR NOT NULL,
            side             VARCHAR NOT NULL,
            qty              INTEGER NOT NULL,
            price            DOUBLE NOT NULL,
            order_type       VARCHAR DEFAULT 'market',
            status           VARCHAR DEFAULT 'filled',
            conviction_score DOUBLE DEFAULT 0,
            signal           VARCHAR DEFAULT '',
            filled_at        TIMESTAMP,
            created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            bot_id           VARCHAR NOT NULL DEFAULT 'default'
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_triggers (
            id              VARCHAR PRIMARY KEY,
            ticker          VARCHAR NOT NULL,
            trigger_type    VARCHAR NOT NULL,
            trigger_price   DOUBLE NOT NULL,
            high_water_mark DOUBLE DEFAULT 0,
            trailing_pct    DOUBLE DEFAULT 0,
            action          VARCHAR DEFAULT 'sell',
            qty             INTEGER NOT NULL,
            status          VARCHAR DEFAULT 'active',
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            bot_id          VARCHAR NOT NULL DEFAULT 'default'
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            timestamp              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            cash_balance           DOUBLE NOT NULL,
            total_positions_value  DOUBLE DEFAULT 0,
            total_portfolio_value  DOUBLE DEFAULT 0,
            realized_pnl           DOUBLE DEFAULT 0,
            unrealized_pnl         DOUBLE DEFAULT 0,
            bot_id                 VARCHAR NOT NULL DEFAULT 'default'
        );
    """)

    # ── Activity Log: pipeline_events audit trail ─────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pipeline_events (
            id          VARCHAR PRIMARY KEY,
            timestamp   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            phase       VARCHAR NOT NULL,
            event_type  VARCHAR NOT NULL,
            ticker      VARCHAR,
            detail      VARCHAR NOT NULL,
            metadata    VARCHAR DEFAULT '{}',
            loop_id     VARCHAR,
            status      VARCHAR DEFAULT 'success',
            bot_id      VARCHAR DEFAULT 'default',
            model_name  VARCHAR DEFAULT ''
        );
    """)

    # ── Phase 4: Scheduler & Reports ─────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scheduler_runs (
            id            VARCHAR PRIMARY KEY,
            job_name      VARCHAR NOT NULL,
            started_at    TIMESTAMP NOT NULL,
            completed_at  TIMESTAMP,
            status        VARCHAR DEFAULT 'running',
            summary       VARCHAR DEFAULT '',
            error         VARCHAR DEFAULT ''
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id            VARCHAR PRIMARY KEY,
            report_type   VARCHAR NOT NULL,
            report_date   DATE NOT NULL,
            content       VARCHAR NOT NULL,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # ── Phase 5: Smart Money tables (13F + Congressional) ────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sec_13f_filers (
            cik            VARCHAR PRIMARY KEY,
            filer_name     VARCHAR NOT NULL,
            last_checked   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active      BOOLEAN DEFAULT TRUE
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS sec_13f_holdings (
            cik            VARCHAR NOT NULL,
            ticker         VARCHAR NOT NULL,
            name_of_issuer VARCHAR,
            cusip          VARCHAR,
            value_usd      DOUBLE,
            shares         BIGINT,
            share_type     VARCHAR,
            filing_quarter VARCHAR NOT NULL,
            filing_date    DATE,
            collected_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (cik, ticker, filing_quarter)
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS congressional_trades (
            id             VARCHAR PRIMARY KEY,
            member_name    VARCHAR NOT NULL,
            chamber        VARCHAR NOT NULL,
            ticker         VARCHAR,
            asset_name     VARCHAR,
            tx_type        VARCHAR NOT NULL,
            tx_date        DATE,
            filed_date     DATE,
            amount_range   VARCHAR,
            source_url     VARCHAR DEFAULT '',
            collected_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS news_full_articles (
            article_hash   VARCHAR PRIMARY KEY,
            title          VARCHAR NOT NULL,
            url            VARCHAR NOT NULL,
            publisher      VARCHAR DEFAULT '',
            published_at   TIMESTAMP,
            summary        VARCHAR DEFAULT '',
            content        TEXT NOT NULL,
            content_length INTEGER DEFAULT 0,
            tickers_found  VARCHAR DEFAULT '',
            source_feed    VARCHAR DEFAULT '',
            collected_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # ── Multi-Bot Leaderboard tables ────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bots (
            bot_id          VARCHAR PRIMARY KEY,
            model_name      VARCHAR NOT NULL,
            display_name    VARCHAR DEFAULT '',
            provider        VARCHAR DEFAULT 'ollama',
            provider_url    VARCHAR DEFAULT 'http://localhost:11434',
            context_length  INTEGER DEFAULT 8192,
            temperature     DOUBLE DEFAULT 0.3,
            top_p           DOUBLE DEFAULT 1.0,
            max_tokens      INTEGER DEFAULT 0,
            eval_batch_size    INTEGER DEFAULT 512,
            flash_attention    BOOLEAN DEFAULT TRUE,
            num_experts        INTEGER DEFAULT 0,
            gpu_offload        BOOLEAN DEFAULT TRUE,
            total_trades     INTEGER DEFAULT 0,
            total_pnl        DOUBLE DEFAULT 0.0,
            win_rate         DOUBLE DEFAULT 0.0,
            best_trade_pnl   DOUBLE DEFAULT 0.0,
            worst_trade_pnl  DOUBLE DEFAULT 0.0,
            sharpe_ratio     DOUBLE DEFAULT 0.0,
            max_drawdown     DOUBLE DEFAULT 0.0,
            status           VARCHAR DEFAULT 'active',
            created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_run_at      TIMESTAMP
        );
    """)

    # ── Alpha Attribution: Source Credibility tracking ─────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS source_credibility (
            source_id       VARCHAR PRIMARY KEY,
            source_type     VARCHAR NOT NULL,
            win_count       INTEGER DEFAULT 0,
            loss_count      INTEGER DEFAULT 0,
            total_pnl       DOUBLE DEFAULT 0.0,
            trust_score     DOUBLE DEFAULT 0.5,
            last_updated    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # ── User Exclusions: persisted delete-from-scoreboard ────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_exclusions (
            symbol     VARCHAR NOT NULL,
            bot_id     VARCHAR NOT NULL DEFAULT 'default',
            reason     VARCHAR DEFAULT '',
            created_by VARCHAR DEFAULT 'user',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (symbol, bot_id)
        );
    """)

    # ── Rejected Symbols: quarantine log for filter rejects ──────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rejected_symbols (
            symbol       VARCHAR NOT NULL,
            source       VARCHAR NOT NULL,
            reason       VARCHAR NOT NULL,
            raw_context  VARCHAR DEFAULT '',
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # ── Phase 3+4: Trade Decision Audit Trail ───────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trade_decisions (
            id               VARCHAR PRIMARY KEY,
            bot_id           VARCHAR NOT NULL,
            symbol           VARCHAR NOT NULL,
            ts               TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            action           VARCHAR NOT NULL,
            confidence       DOUBLE,
            rationale        TEXT,
            risk_level       VARCHAR DEFAULT 'MED',
            risk_notes       TEXT DEFAULT '',
            time_horizon     VARCHAR DEFAULT 'SWING',
            raw_llm_response TEXT,
            status           VARCHAR DEFAULT 'pending',
            rejection_reason TEXT DEFAULT ''
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS trade_executions (
            id               VARCHAR PRIMARY KEY,
            decision_id      VARCHAR NOT NULL,
            order_id         VARCHAR DEFAULT '',
            ts               TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            filled_qty       INTEGER DEFAULT 0,
            avg_price        DOUBLE DEFAULT 0,
            status           VARCHAR DEFAULT 'pending',
            broker_error     TEXT DEFAULT ''
        );
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS llm_audit_logs (
            id               VARCHAR PRIMARY KEY,
            cycle_id         VARCHAR DEFAULT '',
            ticker           VARCHAR DEFAULT '',
            agent_step       VARCHAR DEFAULT '',
            system_prompt    TEXT DEFAULT '',
            user_context     TEXT DEFAULT '',
            raw_response     TEXT DEFAULT '',
            parsed_json      TEXT,
            tokens_used      INTEGER DEFAULT 0,
            execution_time_ms INTEGER DEFAULT 0,
            model            VARCHAR DEFAULT '',
            created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # ── Circuit Breaker: daily drawdown kill switch ──────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS circuit_breaker_state (
            bot_id      VARCHAR PRIMARY KEY,
            is_tripped  BOOLEAN DEFAULT FALSE,
            tripped_at  TIMESTAMP,
            reason      VARCHAR DEFAULT '',
            reset_at    TIMESTAMP
        );
    """)

    # ── RAG: Embedding vectors for retrieval-augmented generation ──
    conn.execute("""
        CREATE SEQUENCE IF NOT EXISTS embeddings_seq START 1;
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS embeddings (
            id          INTEGER PRIMARY KEY DEFAULT nextval('embeddings_seq'),
            source_type VARCHAR NOT NULL,
            source_id   VARCHAR NOT NULL,
            ticker      VARCHAR,
            chunk_index INTEGER NOT NULL,
            chunk_text  TEXT NOT NULL,
            embedding   FLOAT[] NOT NULL,
            metadata    VARCHAR DEFAULT '',
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (source_type, source_id, chunk_index)
        );
    """)

    logger.info("DuckDB tables initialized")

    # ---- Schema migrations for existing databases ----
    # These handle DBs created before Phase 8 that are missing new columns.
    # ALTER TABLE ADD COLUMN is idempotent — DuckDB will error on duplicates,
    # which we safely catch.
    _migrate_columns(conn)


def _migrate_columns(conn: duckdb.DuckDBPyConnection) -> None:
    """Add missing columns to existing tables (safe for fresh DBs too)."""

    def _add_col(table: str, col: str, dtype: str) -> None:
        """Try to add a column; silently ignore if it already exists."""
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {dtype}")
            logger.info("Migration: added %s.%s (%s)", table, col, dtype)
        except Exception:
            pass  # Column already exists

    # ---- news_articles: source column ----
    _add_col("news_articles", "source", "VARCHAR DEFAULT 'yfinance'")

    # ---- discovered_tickers: source_url column ----
    _add_col("discovered_tickers", "source_url", "VARCHAR DEFAULT ''")

    # ---- technicals: Phase 8 expanded columns ----
    tech_cols = [
        # EMAs
        ("ema_9", "DOUBLE"), ("ema_21", "DOUBLE"),
        ("ema_50", "DOUBLE"), ("ema_200", "DOUBLE"),
        # Momentum
        ("cci", "DOUBLE"), ("willr", "DOUBLE"), ("mfi", "DOUBLE"),
        ("roc", "DOUBLE"), ("mom", "DOUBLE"), ("ao", "DOUBLE"),
        ("tsi", "DOUBLE"), ("uo", "DOUBLE"), ("stochrsi_k", "DOUBLE"),
        # Trend
        ("adx", "DOUBLE"), ("adx_dmp", "DOUBLE"), ("adx_dmn", "DOUBLE"),
        ("aroon_up", "DOUBLE"), ("aroon_down", "DOUBLE"),
        ("aroon_osc", "DOUBLE"), ("supertrend", "DOUBLE"),
        ("psar", "DOUBLE"), ("chop", "DOUBLE"),
        ("vortex_pos", "DOUBLE"), ("vortex_neg", "DOUBLE"),
        # Volatility
        ("natr", "DOUBLE"), ("true_range", "DOUBLE"),
        ("donchian_upper", "DOUBLE"), ("donchian_lower", "DOUBLE"),
        ("donchian_mid", "DOUBLE"), ("kc_upper", "DOUBLE"),
        ("kc_lower", "DOUBLE"),
        # Volume
        ("obv", "DOUBLE"), ("ad", "DOUBLE"), ("cmf", "DOUBLE"),
        ("efi", "DOUBLE"), ("pvt", "DOUBLE"),
        # Statistics
        ("zscore", "DOUBLE"), ("skew", "DOUBLE"),
        ("kurtosis", "DOUBLE"), ("entropy", "DOUBLE"),
        # Ichimoku
        ("ichi_conv", "DOUBLE"), ("ichi_base", "DOUBLE"),
        ("ichi_span_a", "DOUBLE"), ("ichi_span_b", "DOUBLE"),
        # Fibonacci
        ("fib_0", "DOUBLE"), ("fib_236", "DOUBLE"),
        ("fib_382", "DOUBLE"), ("fib_500", "DOUBLE"),
        ("fib_618", "DOUBLE"), ("fib_786", "DOUBLE"), ("fib_1", "DOUBLE"),
        # Full JSON blob
        ("all_indicators_json", "VARCHAR"),
    ]

    for col, dtype in tech_cols:
        _add_col("technicals", col, dtype)

    # ---- youtube_transcripts: scan tracking column ----
    _add_col("youtube_transcripts", "scanned_for_tickers", "BOOLEAN DEFAULT FALSE")

    # ---- quant_scorecards: PhD-level signal columns ----
    quant_cols = [
        ("momentum_12m", "DOUBLE DEFAULT 0"),
        ("mean_reversion_score", "DOUBLE DEFAULT 0"),
        ("hurst_exponent", "DOUBLE DEFAULT 0.5"),
        ("vwap_deviation", "DOUBLE DEFAULT 0"),
        ("fama_french_alpha", "DOUBLE DEFAULT 0"),
        ("earnings_yield_gap", "DOUBLE DEFAULT 0"),
        ("altman_z_score", "DOUBLE DEFAULT 0"),
        ("piotroski_f_score", "INTEGER DEFAULT 0"),
    ]
    for col, dtype in quant_cols:
        _add_col("quant_scorecards", col, dtype)

    # ---- Multi-Bot: add bot_id to trading tables ----
    bot_id_tables = [
        "positions", "orders", "portfolio_snapshots",
        "price_triggers", "watchlist", "pipeline_events",
    ]
    for tbl in bot_id_tables:
        _add_col(tbl, "bot_id", "VARCHAR DEFAULT 'default'")

    # ---- pipeline_events: model_name for activity log tracking ----
    _add_col("pipeline_events", "model_name", "VARCHAR DEFAULT ''")

    # ---- bots: queue ordering for Run All ----
    _add_col("bots", "queue_order", "INTEGER DEFAULT 0")

    # ---- sec_13f_filers: filing schedule tracking ----
    _add_col("sec_13f_filers", "latest_quarter", "VARCHAR")
    _add_col("sec_13f_filers", "next_expected_filing", "DATE")

    # ---- Fix contaminated last_analyzed timestamps ----
    # Prior bug: _update_watchlist ran UPDATE WHERE ticker = ? (no bot_id),
    # so Bot A's analysis stamped ALL bots' rows.  Reset last_analyzed
    # on rows where the bot never actually ran analysis (analysis_count = 0).
    try:
        fixed = conn.execute(
            "UPDATE watchlist SET last_analyzed = NULL "
            "WHERE last_analyzed IS NOT NULL AND analysis_count = 0"
        ).rowcount
        if fixed:
            logger.info(
                "Migration: reset %d contaminated last_analyzed values", fixed,
            )
    except Exception:
        pass

    # ---- Watchlist: migrate from single-column PK to composite PK ----
    # Existing DBs have ticker as sole PK, which crashes multi-bot imports.
    # Recreate the table with (ticker, bot_id) as the composite PK.
    try:
        # Check if watchlist still has old single-column PK
        info = conn.execute(
            "SELECT column_name FROM information_schema.key_column_usage "
            "WHERE table_name = 'watchlist'"
        ).fetchall()
        pk_cols = [r[0] for r in info]
        if pk_cols and "bot_id" not in pk_cols:
            logger.info("Migration: rebuilding watchlist with composite PK (ticker, bot_id)")
            conn.execute("ALTER TABLE watchlist RENAME TO watchlist_old")
            conn.execute("""
                CREATE TABLE watchlist (
                    ticker          VARCHAR NOT NULL,
                    source          VARCHAR DEFAULT 'manual',
                    added_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_analyzed   TIMESTAMP,
                    analysis_count  INTEGER DEFAULT 0,
                    signal          VARCHAR DEFAULT 'PENDING',
                    confidence      DOUBLE DEFAULT 0.0,
                    discovery_score DOUBLE DEFAULT 0.0,
                    sentiment_hint  VARCHAR DEFAULT 'neutral',
                    status          VARCHAR DEFAULT 'active',
                    cooldown_until  TIMESTAMP,
                    notes           VARCHAR DEFAULT '',
                    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    bot_id          VARCHAR DEFAULT 'default',
                    PRIMARY KEY (ticker, bot_id)
                )
            """)
            conn.execute("""
                INSERT INTO watchlist
                SELECT ticker, source, added_at, last_analyzed,
                       analysis_count, signal, confidence,
                       discovery_score, sentiment_hint, status,
                       cooldown_until, notes, updated_at,
                       COALESCE(bot_id, 'default')
                FROM watchlist_old
            """)
            conn.execute("DROP TABLE watchlist_old")
            logger.info("Migration: watchlist PK migrated to (ticker, bot_id)")
    except Exception as exc:
        logger.warning("Migration: watchlist PK check skipped — %s", exc)

