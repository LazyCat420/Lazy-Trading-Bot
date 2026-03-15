"""Database Duplicate Data Audit Tests.

These tests connect to the live DuckDB database and check for duplicate
data across all major tables. Run with:

    pytest tests/test_db_duplicates.py -v

Each test reports the duplicate count and will WARN (not fail) if
duplicates exceed a threshold, so you can monitor data quality over time.
"""
from __future__ import annotations

import os
import warnings
from datetime import datetime

import duckdb
import pytest


# ── Fixture: read-only DuckDB connection ─────────────────────────────

DB_PATH = os.path.join(
    os.path.dirname(__file__), "..", "data", "trading_bot.duckdb",
)


@pytest.fixture(scope="module")
def db():
    """Open a read-only connection to the trading bot DB."""
    path = os.path.abspath(DB_PATH)
    if not os.path.exists(path):
        pytest.skip(f"Database not found: {path}")

    # Try read-only first; fall back to a temp copy if locked.
    try:
        conn = duckdb.connect(path, read_only=True)
        conn.execute("SELECT 1").fetchone()
    except Exception:
        import shutil, tempfile
        tmp = os.path.join(tempfile.gettempdir(), "audit_bot_test.duckdb")
        shutil.copy2(path, tmp)
        conn = duckdb.connect(tmp, read_only=True)

    yield conn
    conn.close()


# ── Helper ───────────────────────────────────────────────────────────

def _table_exists(db, table: str) -> bool:
    return bool(
        db.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='main' AND table_name=?",
            [table],
        ).fetchone()
    )


def _row_count(db, table: str) -> int:
    return db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]


# ── Tests ────────────────────────────────────────────────────────────


class TestDiscoveredTickersDuplicates:
    """discovered_tickers should not have identical (ticker, source, snippet) rows."""

    def test_total_vs_distinct(self, db):
        if not _table_exists(db, "discovered_tickers"):
            pytest.skip("Table does not exist")

        total = _row_count(db, "discovered_tickers")
        distinct = db.execute(
            "SELECT count(DISTINCT (ticker, source, context_snippet)) "
            "FROM discovered_tickers"
        ).fetchone()[0]
        dup_pct = ((total - distinct) / total * 100) if total else 0

        print(f"\n  discovered_tickers: {total} total, {distinct} distinct, "
              f"{total - distinct} dupes ({dup_pct:.0f}%)")

        if dup_pct > 30:
            warnings.warn(
                f"discovered_tickers has {dup_pct:.0f}% duplicates "
                f"({total - distinct} rows)",
                stacklevel=2,
            )

    def test_worst_offenders(self, db):
        if not _table_exists(db, "discovered_tickers"):
            pytest.skip("Table does not exist")

        rows = db.execute("""
            SELECT ticker, source, COUNT(*) as cnt,
                   COUNT(DISTINCT context_snippet) as unique_snippets
            FROM discovered_tickers
            GROUP BY ticker, source
            HAVING COUNT(*) > 5
            ORDER BY cnt DESC
            LIMIT 10
        """).fetchall()

        print("\n  Top re-discovered tickers:")
        for r in rows:
            print(f"    {r[0]:8s} src={r[1]:15s} count={r[2]:>3} "
                  f"unique_snippets={r[3]:>2}")

        if rows and rows[0][2] > 20:
            warnings.warn(
                f"{rows[0][0]} discovered {rows[0][2]}x with only "
                f"{rows[0][3]} unique snippets",
                stacklevel=2,
            )

    def test_same_day_same_source_duplicates(self, db):
        """Check if same ticker+source appears multiple times on the same day."""
        if not _table_exists(db, "discovered_tickers"):
            pytest.skip("Table does not exist")

        same_day_dupes = db.execute("""
            SELECT ticker, source,
                   date_trunc('day', discovered_at) as day,
                   COUNT(*) as cnt
            FROM discovered_tickers
            GROUP BY ticker, source, date_trunc('day', discovered_at)
            HAVING COUNT(*) > 1
            ORDER BY cnt DESC
            LIMIT 10
        """).fetchall()

        total_same_day = len(same_day_dupes)
        print(f"\n  Same-day same-source duplicates: {total_same_day} groups")
        for r in same_day_dupes[:5]:
            print(f"    {r[0]:8s} src={r[1]:15s} date={str(r[2])[:10]} "
                  f"count={r[3]}")

        # This SHOULD be 0 with the dedup guard
        if total_same_day > 0:
            warnings.warn(
                f"{total_same_day} same-day duplicate groups found — "
                f"dedup guard may be broken",
                stacklevel=2,
            )


class TestQuantScorecardDuplicates:
    """quant_scorecards should not have unbounded duplicates per ticker."""

    def test_scorecard_accumulation(self, db):
        if not _table_exists(db, "quant_scorecards"):
            pytest.skip("Table does not exist")

        total = _row_count(db, "quant_scorecards")
        distinct_tickers = db.execute(
            "SELECT count(DISTINCT ticker) FROM quant_scorecards"
        ).fetchone()[0]
        avg_per_ticker = total / distinct_tickers if distinct_tickers else 0

        print(f"\n  quant_scorecards: {total} rows, {distinct_tickers} tickers, "
              f"{avg_per_ticker:.1f} avg/ticker")

        rows = db.execute("""
            SELECT ticker, COUNT(*) as cnt
            FROM quant_scorecards
            GROUP BY ticker
            ORDER BY cnt DESC
            LIMIT 5
        """).fetchall()
        print("  Top accumulators:")
        for r in rows:
            print(f"    {r[0]:8s} {r[1]:>3} scorecards")

        if avg_per_ticker > 10:
            warnings.warn(
                f"quant_scorecards averaging {avg_per_ticker:.0f} rows per "
                f"ticker — no dedup guard",
                stacklevel=2,
            )


class TestTickerDossierDuplicates:
    """ticker_dossiers should not have unbounded duplicates per ticker."""

    def test_dossier_accumulation(self, db):
        if not _table_exists(db, "ticker_dossiers"):
            pytest.skip("Table does not exist")

        total = _row_count(db, "ticker_dossiers")
        distinct_tickers = db.execute(
            "SELECT count(DISTINCT ticker) FROM ticker_dossiers"
        ).fetchone()[0]
        avg_per_ticker = total / distinct_tickers if distinct_tickers else 0

        print(f"\n  ticker_dossiers: {total} rows, {distinct_tickers} tickers, "
              f"{avg_per_ticker:.1f} avg/ticker")

        rows = db.execute("""
            SELECT ticker, COUNT(*) as cnt
            FROM ticker_dossiers
            GROUP BY ticker
            ORDER BY cnt DESC
            LIMIT 5
        """).fetchall()
        print("  Top accumulators:")
        for r in rows:
            print(f"    {r[0]:8s} {r[1]:>3} dossiers")

        if avg_per_ticker > 10:
            warnings.warn(
                f"ticker_dossiers averaging {avg_per_ticker:.0f} rows per "
                f"ticker — no dedup guard",
                stacklevel=2,
            )


class TestNewsArticleDuplicates:
    """news_articles should not have duplicate titles."""

    def test_title_duplicates(self, db):
        if not _table_exists(db, "news_articles"):
            pytest.skip("Table does not exist")

        total = _row_count(db, "news_articles")
        distinct_titles = db.execute(
            "SELECT count(DISTINCT title) FROM news_articles"
        ).fetchone()[0]
        dup_count = total - distinct_titles
        dup_pct = (dup_count / total * 100) if total else 0

        print(f"\n  news_articles: {total} total, {distinct_titles} distinct "
              f"titles, {dup_count} dupes ({dup_pct:.1f}%)")

        if dup_pct > 5:
            warnings.warn(
                f"news_articles has {dup_pct:.1f}% duplicate titles "
                f"({dup_count} rows)",
                stacklevel=2,
            )

    def test_worst_duplicate_titles(self, db):
        if not _table_exists(db, "news_articles"):
            pytest.skip("Table does not exist")

        rows = db.execute("""
            SELECT title, COUNT(*) as cnt
            FROM news_articles
            GROUP BY title
            HAVING COUNT(*) > 3
            ORDER BY cnt DESC
            LIMIT 10
        """).fetchall()

        print("\n  Most duplicated news titles:")
        for r in rows:
            print(f"    '{(r[0] or '')[:50]}' count={r[1]}")


class TestYouTubeTranscriptDuplicates:
    """youtube_transcripts should not have duplicate video_ids."""

    def test_video_id_duplicates(self, db):
        if not _table_exists(db, "youtube_transcripts"):
            pytest.skip("Table does not exist")

        total = _row_count(db, "youtube_transcripts")
        distinct = db.execute(
            "SELECT count(DISTINCT video_id) FROM youtube_transcripts"
        ).fetchone()[0]
        dup_count = total - distinct

        print(f"\n  youtube_transcripts: {total} total, {distinct} distinct "
              f"video_ids, {dup_count} dupes")

        if dup_count > 0:
            rows = db.execute("""
                SELECT video_id, title, COUNT(*) as cnt
                FROM youtube_transcripts
                GROUP BY video_id, title
                HAVING COUNT(*) > 1
                ORDER BY cnt DESC
                LIMIT 5
            """).fetchall()
            print("  Duplicate videos:")
            for r in rows:
                print(f"    {r[0]:15s} '{(r[1] or '')[:40]}' count={r[2]}")

            warnings.warn(
                f"youtube_transcripts has {dup_count} duplicate video rows",
                stacklevel=2,
            )


class TestPriceHistoryClean:
    """price_history should have 0 (ticker, date) duplicates."""

    def test_no_duplicates(self, db):
        if not _table_exists(db, "price_history"):
            pytest.skip("Table does not exist")

        dup_count = db.execute("""
            SELECT SUM(cnt - 1) FROM (
                SELECT ticker, date, COUNT(*) as cnt
                FROM price_history
                GROUP BY ticker, date
                HAVING COUNT(*) > 1
            )
        """).fetchone()[0]

        total = _row_count(db, "price_history")
        print(f"\n  price_history: {total} rows, {dup_count or 0} duplicates")
        assert (dup_count or 0) == 0, f"price_history has {dup_count} duplicate rows!"


class TestTechnicalsClean:
    """technicals should have 0 (ticker, date) duplicates."""

    def test_no_duplicates(self, db):
        if not _table_exists(db, "technicals"):
            pytest.skip("Table does not exist")

        dup_count = db.execute("""
            SELECT SUM(cnt - 1) FROM (
                SELECT ticker, date, COUNT(*) as cnt
                FROM technicals
                GROUP BY ticker, date
                HAVING COUNT(*) > 1
            )
        """).fetchone()[0]

        total = _row_count(db, "technicals")
        print(f"\n  technicals: {total} rows, {dup_count or 0} duplicates")
        assert (dup_count or 0) == 0, f"technicals has {dup_count} duplicate rows!"


class TestCongressionalTradesClean:
    """congressional_trades should have no exact duplicates."""

    def test_no_duplicates(self, db):
        if not _table_exists(db, "congressional_trades"):
            pytest.skip("Table does not exist")

        total = _row_count(db, "congressional_trades")
        distinct = db.execute(
            "SELECT count(DISTINCT (member_name, ticker, tx_date, amount_range)) "
            "FROM congressional_trades"
        ).fetchone()[0]
        dup_count = total - distinct

        print(f"\n  congressional_trades: {total} total, {distinct} distinct, "
              f"{dup_count} dupes")
        assert dup_count == 0, f"congressional_trades has {dup_count} duplicate rows!"


class TestEmbeddingDedup:
    """Embeddings should not have duplicate (source_type, source_id, chunk_index) combos."""

    def test_embedding_dedup(self, db):
        if not _table_exists(db, "embeddings"):
            pytest.skip("Table does not exist")

        total = _row_count(db, "embeddings")
        distinct_sources = db.execute(
            "SELECT count(DISTINCT (source_type, source_id)) FROM embeddings"
        ).fetchone()[0]

        print(f"\n  embeddings: {total} chunks from {distinct_sources} sources "
              f"({total/distinct_sources:.1f} avg chunks/source)")

        # Check for actual cross-run duplicates (same source embedded multiple times)
        by_type = db.execute("""
            SELECT source_type, COUNT(*) as chunks,
                   COUNT(DISTINCT source_id) as sources
            FROM embeddings
            GROUP BY source_type
            ORDER BY chunks DESC
        """).fetchall()
        for r in by_type:
            print(f"    {r[0]:15s} {r[1]:>6} chunks from {r[2]:>4} sources")


class TestDatabaseSummary:
    """Print a full summary of all table sizes for quick health check."""

    def test_table_sizes(self, db):
        tables = db.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='main'"
        ).fetchall()

        print(f"\n  {'TABLE':40s} {'ROWS':>10}")
        print(f"  {'-'*40} {'-'*10}")
        total_rows = 0
        for t in sorted(tables, key=lambda x: x[0]):
            try:
                count = _row_count(db, t[0])
                total_rows += count
                print(f"  {t[0]:40s} {count:>10,}")
            except Exception:
                print(f"  {t[0]:40s}  ERROR")
        print(f"  {'-'*40} {'-'*10}")
        print(f"  {'TOTAL':40s} {total_rows:>10,}")
