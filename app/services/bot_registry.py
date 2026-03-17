"""Bot Registry — manages multi-bot lifecycle, settings, and leaderboard.

Each bot represents an LLM configuration with its own isolated portfolio,
watchlist, and trading history. The leaderboard computes performance
rankings across all bots.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.database import get_db
from app.utils.logger import logger


class BotRegistry:
    """CRUD and analytics for the bots table."""

    # ── Ensure ─────────────────────────────────────────────────

    @staticmethod
    def ensure_bot_exists(
        bot_id: str,
        model_name: str = "",
        display_name: str = "",
        *,
        provider: str = "ollama",
        provider_url: str = "http://localhost:11434",
    ) -> dict[str, Any]:
        """Guarantee a bot row exists in the `bots` table.

        If the bot_id already exists, return its row unchanged.
        If it does NOT exist, insert a new row so that `update_stats`,
        `record_run`, and the leaderboard query can all find it.
        """
        existing = BotRegistry.get_bot(bot_id)
        if existing:
            return existing

        # Auto-register with sensible defaults
        if not model_name:
            model_name = bot_id
        if not display_name:
            display_name = model_name.split("/")[-1]

        conn = get_db()
        conn.execute(
            """
            INSERT INTO bots (
                bot_id, model_name, display_name, provider, provider_url,
                context_length, temperature, top_p, max_tokens,
                eval_batch_size, flash_attention, num_experts, gpu_offload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                bot_id, model_name, display_name, provider, provider_url,
                8192, 0.3, 1.0, 0, 512, True, 0, True,
            ],
        )
        logger.info(
            "[BotRegistry] Auto-registered bot %s (%s) for leaderboard",
            bot_id, display_name,
        )
        return BotRegistry.get_bot(bot_id)  # type: ignore[return-value]

    # ── Create ─────────────────────────────────────────────────

    @staticmethod
    def register_bot(
        model_name: str,
        display_name: str = "",
        *,
        provider: str = "ollama",
        provider_url: str = "http://localhost:11434",
        context_length: int = 8192,
        temperature: float = 0.3,
        top_p: float = 1.0,
        max_tokens: int = 0,
        eval_batch_size: int = 512,
        flash_attention: bool = True,
        num_experts: int = 0,
        gpu_offload: bool = True,
    ) -> dict[str, Any]:
        """Register a new bot with its LLM settings. Returns the bot row."""
        bot_id = uuid.uuid4().hex[:12]
        if not display_name:
            display_name = model_name.split("/")[-1]

        conn = get_db()
        conn.execute(
            """
            INSERT INTO bots (
                bot_id, model_name, display_name, provider, provider_url,
                context_length, temperature, top_p, max_tokens,
                eval_batch_size, flash_attention, num_experts, gpu_offload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                bot_id,
                model_name,
                display_name,
                provider,
                provider_url,
                context_length,
                temperature,
                top_p,
                max_tokens,
                eval_batch_size,
                flash_attention,
                num_experts,
                gpu_offload,
            ],
        )
        logger.info("[BotRegistry] Registered bot %s (%s)", bot_id, display_name)

        # Seed per-model logic loop prompts
        try:
            from app.services.AgenticExtractor import AgenticExtractor
            AgenticExtractor(bot_id=bot_id).seed_all_prompts()
        except Exception as exc:
            logger.warning("[BotRegistry] Failed to seed prompts for %s: %s", bot_id, exc)

        return BotRegistry.get_bot(bot_id)  # type: ignore[return-value]

    # ── Read ───────────────────────────────────────────────────

    @staticmethod
    def get_bot(bot_id: str) -> dict[str, Any] | None:
        """Get a single bot by ID."""
        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM bots WHERE bot_id = ?",
            [bot_id],
        ).fetchall()
        if not rows:
            return None
        cols = [d[0] for d in conn.execute("SELECT * FROM bots LIMIT 0").description]
        return dict(zip(cols, rows[0]))

    @staticmethod
    def list_bots(*, include_inactive: bool = False) -> list[dict[str, Any]]:
        """List all bots, optionally including deactivated ones."""
        conn = get_db()
        sql = "SELECT * FROM bots"
        if not include_inactive:
            sql += " WHERE status = 'active'"
        sql += " ORDER BY queue_order ASC, created_at DESC"
        rows = conn.execute(sql).fetchall()
        if not rows:
            return []
        cols = [d[0] for d in conn.execute("SELECT * FROM bots LIMIT 0").description]
        return [dict(zip(cols, r)) for r in rows]

    # ── Delete ──────────────────────────────────────────────────

    @staticmethod
    def delete_bot(bot_id: str, *, hard: bool = False) -> bool:
        """Delete a bot.

        Soft delete: sets status='deleted' (excluded from list_bots).
        Hard delete: removes the bot row AND all related data from
        positions, orders, portfolio_snapshots, price_triggers,
        watchlist, and pipeline_events.

        Returns True if a bot was found and deleted.
        """
        conn = get_db()
        existing = conn.execute(
            "SELECT bot_id FROM bots WHERE bot_id = ?", [bot_id],
        ).fetchone()
        if not existing:
            return False

        if hard:
            # Cascade: delete related data first
            for table in (
                "positions",
                "orders",
                "portfolio_snapshots",
                "price_triggers",
                "watchlist",
                "pipeline_events",
                "model_logic_loops",
            ):
                try:
                    conn.execute(
                        f"DELETE FROM {table} WHERE bot_id = ?",
                        [bot_id],
                    )
                except Exception:
                    pass  # Table may not exist or lack bot_id column
            conn.execute("DELETE FROM bots WHERE bot_id = ?", [bot_id])
            logger.info(
                "[BotRegistry] Hard-deleted bot %s and related data",
                bot_id,
            )
        else:
            conn.execute(
                "UPDATE bots SET status = 'deleted' WHERE bot_id = ?",
                [bot_id],
            )
            logger.info("[BotRegistry] Soft-deleted bot %s", bot_id)
        return True

    # ── Update ─────────────────────────────────────────────────

    @staticmethod
    def update_bot_settings(bot_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        """Update LLM settings for a bot. Returns updated bot."""
        allowed = {
            "display_name",
            "provider",
            "provider_url",
            "context_length",
            "temperature",
            "top_p",
            "max_tokens",
            "eval_batch_size",
            "flash_attention",
            "num_experts",
            "gpu_offload",
            "status",
        }
        updates = {k: v for k, v in data.items() if k in allowed}
        if not updates:
            return BotRegistry.get_bot(bot_id)

        conn = get_db()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [bot_id]
        conn.execute(f"UPDATE bots SET {set_clause} WHERE bot_id = ?", values)
        logger.info("[BotRegistry] Updated bot %s: %s", bot_id, list(updates.keys()))
        return BotRegistry.get_bot(bot_id)

    @staticmethod
    def record_run(bot_id: str) -> None:
        """Update last_run_at timestamp."""
        conn = get_db()
        conn.execute(
            "UPDATE bots SET last_run_at = CURRENT_TIMESTAMP WHERE bot_id = ?",
            [bot_id],
        )

    @staticmethod
    def update_stats(bot_id: str) -> None:
        """Recalculate and persist performance stats from order/snapshot data."""
        conn = get_db()

        # Total trades
        total = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE bot_id = ?",
            [bot_id],
        ).fetchone()[0]

        # Win rate (sells with realized_pnl > 0)
        sells = conn.execute(
            """SELECT COUNT(*) FROM orders
               WHERE bot_id = ? AND side = 'sell'""",
            [bot_id],
        ).fetchone()[0]

        wins = conn.execute(
            """SELECT COUNT(*) FROM orders
               WHERE bot_id = ? AND side = 'sell'
               AND CAST(
                   json_extract_string(
                       CASE WHEN signal LIKE '{%' THEN signal ELSE '{}' END,
                       '$.realized_pnl'
                   ) AS DOUBLE
               ) > 0""",
            [bot_id],
        ).fetchone()[0]

        win_rate = (wins / sells * 100) if sells > 0 else 0.0

        # Total P&L = current portfolio value minus starting balance
        # This captures BOTH realized gains from sells AND unrealized
        # gains from held positions.
        latest_snap = conn.execute(
            """SELECT total_portfolio_value
               FROM portfolio_snapshots
               WHERE bot_id = ?
               ORDER BY timestamp DESC LIMIT 1""",
            [bot_id],
        ).fetchone()

        first_snap = conn.execute(
            """SELECT total_portfolio_value
               FROM portfolio_snapshots
               WHERE bot_id = ?
               ORDER BY timestamp ASC LIMIT 1""",
            [bot_id],
        ).fetchone()

        current_value = latest_snap[0] if latest_snap and latest_snap[0] else 0.0
        starting_value = first_snap[0] if first_snap and first_snap[0] else 0.0
        total_pnl = current_value - starting_value

        # Max drawdown from snapshots
        snap_values = conn.execute(
            """SELECT total_portfolio_value
               FROM portfolio_snapshots
               WHERE bot_id = ?
               ORDER BY timestamp""",
            [bot_id],
        ).fetchall()

        max_dd = 0.0
        peak = 0.0
        for (val,) in snap_values:
            if val and val > peak:
                peak = val
            if peak > 0 and val:
                dd = (peak - val) / peak
                if dd > max_dd:
                    max_dd = dd

        conn.execute(
            """UPDATE bots SET
                total_trades = ?,
                total_pnl = ?,
                win_rate = ?,
                max_drawdown = ?
               WHERE bot_id = ?""",
            [total, total_pnl, win_rate, max_dd, bot_id],
        )

    # ── Reorder ────────────────────────────────────────────────

    @staticmethod
    def reorder_bots(order: list[str]) -> None:
        """Set queue_order for bots. order is a list of bot_ids."""
        conn = get_db()
        for idx, bot_id in enumerate(order):
            conn.execute(
                "UPDATE bots SET queue_order = ? WHERE bot_id = ?",
                [idx, bot_id],
            )
        logger.info("[BotRegistry] Reordered %d bots", len(order))

    # ── Delete / Deactivate ────────────────────────────────────

    @staticmethod
    def deactivate_bot(bot_id: str) -> bool:
        """Soft-delete: set status to 'inactive'."""
        conn = get_db()
        conn.execute(
            "UPDATE bots SET status = 'inactive' WHERE bot_id = ?",
            [bot_id],
        )
        logger.info("[BotRegistry] Deactivated bot %s", bot_id)
        return True

    # ── Leaderboard ────────────────────────────────────────────

    @staticmethod
    def get_leaderboard() -> list[dict[str, Any]]:
        """Return all active bots ranked by total portfolio value descending."""
        conn = get_db()
        rows = conn.execute("""
            SELECT
                bot_id, model_name, display_name,
                total_trades, total_pnl, win_rate,
                best_trade_pnl, worst_trade_pnl,
                sharpe_ratio, max_drawdown,
                context_length, temperature, top_p,
                status, created_at, last_run_at,
                provider, queue_order
            FROM bots
            WHERE status = 'active'
            ORDER BY total_pnl DESC, total_trades DESC, created_at ASC
        """).fetchall()

        if not rows:
            return []

        cols = [
            "bot_id",
            "model_name",
            "display_name",
            "total_trades",
            "total_pnl",
            "win_rate",
            "best_trade_pnl",
            "worst_trade_pnl",
            "sharpe_ratio",
            "max_drawdown",
            "context_length",
            "temperature",
            "top_p",
            "status",
            "created_at",
            "last_run_at",
            "provider",
            "queue_order",
        ]

        result = []
        for i, row in enumerate(rows):
            d = dict(zip(cols, row))
            d["rank"] = i + 1

            # Fetch current total portfolio value and starting balance
            bot_id = d["bot_id"]
            latest = conn.execute(
                """SELECT total_portfolio_value
                   FROM portfolio_snapshots
                   WHERE bot_id = ?
                   ORDER BY timestamp DESC LIMIT 1""",
                [bot_id],
            ).fetchone()
            first = conn.execute(
                """SELECT total_portfolio_value
                   FROM portfolio_snapshots
                   WHERE bot_id = ?
                   ORDER BY timestamp ASC LIMIT 1""",
                [bot_id],
            ).fetchone()

            current_val = latest[0] if latest and latest[0] else 0.0
            starting_val = first[0] if first and first[0] else 0.0
            d["total_portfolio_value"] = round(current_val, 2)
            d["starting_balance"] = round(starting_val, 2)
            d["return_pct"] = (
                round((current_val - starting_val) / starting_val * 100, 2)
                if starting_val > 0
                else 0.0
            )

            # Fetch current holdings (open positions) for this bot
            pos_rows = conn.execute(
                """SELECT ticker, qty, avg_entry_price
                   FROM positions WHERE bot_id = ?""",
                [bot_id],
            ).fetchall()
            d["positions"] = [
                {
                    "ticker": p[0],
                    "qty": p[1],
                    "avg_entry_price": round(p[2], 2),
                }
                for p in pos_rows
            ]
            d["positions_count"] = len(pos_rows)
            result.append(d)
        return result
