"""Congressional Trades Collector — scrapes Senate/House stock transactions.

Scrapes the Senate Electronic Financial Disclosure (eFD) system at
efdsearch.senate.gov for periodic transaction reports filed by U.S. senators.

Data source:
    https://efdsearch.senate.gov/search/report/data/  (POST API)

Based on: https://github.com/neelsomani/senator-filings
Rate limit: 2s between requests (server expectation).
Auth: CSRF token + session cookie (no API key needed).
"""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timedelta
from typing import Any

import requests
from bs4 import BeautifulSoup

from app.database import get_db
from app.models.discovery import ScoredTicker
from app.utils.logger import logger

# ── Senate eFD endpoints ─────────────────────────────────────────────
ROOT = "https://efdsearch.senate.gov"
LANDING_PAGE_URL = f"{ROOT}/search/home/"
SEARCH_PAGE_URL = f"{ROOT}/search/"
REPORTS_URL = f"{ROOT}/search/report/data/"

BATCH_SIZE = 100
RATE_LIMIT_SECS = 2
PDF_PREFIX = "/search/view/paper/"

# Report type 11 = Periodic Transaction Report
REPORT_TYPE_PTR = "[11]"

# Max age of filings to collect (days)
MAX_FILING_AGE_DAYS = 90


class CongressCollector:
    """Collects congressional stock trading data from Senate eFD."""

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        })

    # ── Public: Discovery integration ────────────────────────────────

    async def collect_recent_trades(self) -> list[ScoredTicker]:
        """Scrape recent congressional trades and return tickers as ScoredTicker.

        Called during Discovery phase. Returns unique tickers that
        congress members have recently traded, scored by trade count.
        """
        db = get_db()

        # Daily guard: skip if we already scraped today
        row = db.execute(
            "SELECT COUNT(*) FROM congressional_trades "
            "WHERE collected_at >= CURRENT_DATE"
        ).fetchone()
        if row and row[0] > 0:
            logger.info(
                "[Congress] Already collected today (%d trades), using cache",
                row[0],
            )
            return self._tickers_from_db()

        logger.info("[Congress] Starting congressional trades collection")

        try:
            trades = self._scrape_senate_trades()
            self._save_trades(db, trades)
            logger.info("[Congress] Saved %d trades", len(trades))
        except Exception as e:
            logger.error("[Congress] Senate scraping failed: %s", e)

        return self._tickers_from_db()

    async def get_trades_for_ticker(self, ticker: str) -> list[dict[str, Any]]:
        """Get congressional trades for a specific ticker (pipeline step).

        Returns list of dicts with member name, trade type, date, amount.
        """
        db = get_db()
        rows = db.execute(
            """
            SELECT member_name, chamber, tx_type, tx_date,
                   filed_date, amount_range, asset_name
            FROM congressional_trades
            WHERE ticker = ?
            ORDER BY tx_date DESC
            LIMIT 20
            """,
            [ticker],
        ).fetchall()

        return [
            {
                "member_name": r[0],
                "chamber": r[1],
                "tx_type": r[2],
                "tx_date": str(r[3]) if r[3] else None,
                "filed_date": str(r[4]) if r[4] else None,
                "amount_range": r[5],
                "asset_name": r[6],
            }
            for r in rows
        ]

    # ── Private: Senate eFD scraping ─────────────────────────────────

    def _scrape_senate_trades(self) -> list[dict[str, Any]]:
        """Scrape the Senate eFD for recent periodic transaction reports."""
        # Step 1: Get CSRF token and accept agreement
        csrf_token = self._get_csrf_token()
        if not csrf_token:
            logger.error("[Congress] Failed to get CSRF token")
            return []

        # Step 2: Fetch report listing pages
        all_reports = self._fetch_all_reports(csrf_token)
        logger.info("[Congress] Fetched %d report entries", len(all_reports))

        # Step 3: Parse individual reports for trade details
        all_trades: list[dict[str, Any]] = []
        cutoff_date = datetime.now() - timedelta(days=MAX_FILING_AGE_DAYS)

        for i, report_row in enumerate(all_reports):
            if i >= 200:  # Safety cap
                logger.info("[Congress] Reached 200-report cap, stopping")
                break

            try:
                trades = self._parse_report(report_row, cutoff_date)
                all_trades.extend(trades)
            except Exception as e:
                logger.debug("[Congress] Failed to parse report %d: %s", i, e)

            if i % 20 == 0 and i > 0:
                logger.info(
                    "[Congress] Processed %d/%d reports, %d trades so far",
                    i, len(all_reports), len(all_trades),
                )

        return all_trades

    def _get_csrf_token(self) -> str | None:
        """Get CSRF token by visiting the landing page and accepting terms."""
        try:
            time.sleep(RATE_LIMIT_SECS)
            resp = self._session.get(LANDING_PAGE_URL, timeout=15)

            if resp.url != LANDING_PAGE_URL:
                logger.warning("[Congress] Redirected from landing page: %s", resp.url)

            soup = BeautifulSoup(resp.text, "lxml")
            csrf_input = soup.find(attrs={"name": "csrfmiddlewaretoken"})
            if not csrf_input:
                logger.error("[Congress] No CSRF token found on landing page")
                return None

            form_csrf = csrf_input["value"]  # type: ignore[index]

            # Accept the agreement
            time.sleep(RATE_LIMIT_SECS)
            self._session.post(
                LANDING_PAGE_URL,
                data={
                    "csrfmiddlewaretoken": form_csrf,
                    "prohibition_agreement": "1",
                },
                headers={"Referer": LANDING_PAGE_URL},
            )

            # Get the session CSRF token
            if "csrftoken" in self._session.cookies:
                return self._session.cookies["csrftoken"]
            if "csrf" in self._session.cookies:
                return self._session.cookies["csrf"]

            logger.error("[Congress] No CSRF cookie set after agreement")
            return None

        except Exception as e:
            logger.error("[Congress] CSRF token retrieval failed: %s", e)
            return None

    def _fetch_all_reports(self, token: str) -> list[list[str]]:
        """Paginate through the periodic transaction report API."""
        all_reports: list[list[str]] = []
        offset = 0

        # Calculate date range for recent filings
        start_date = (
            datetime.now() - timedelta(days=MAX_FILING_AGE_DAYS)
        ).strftime("%m/%d/%Y 00:00:00")

        while True:
            time.sleep(RATE_LIMIT_SECS)
            try:
                resp = self._session.post(
                    REPORTS_URL,
                    data={
                        "start": str(offset),
                        "length": str(BATCH_SIZE),
                        "report_types": REPORT_TYPE_PTR,
                        "filer_types": "[]",
                        "submitted_start_date": start_date,
                        "submitted_end_date": "",
                        "candidate_state": "",
                        "senator_state": "",
                        "office_id": "",
                        "first_name": "",
                        "last_name": "",
                        "csrfmiddlewaretoken": token,
                    },
                    headers={"Referer": SEARCH_PAGE_URL},
                    timeout=15,
                )

                data = resp.json().get("data", [])
                if not data:
                    break

                all_reports.extend(data)
                offset += BATCH_SIZE

                # Safety: max 1000 reports (10 pages)
                if offset >= 1000:
                    break

            except Exception as e:
                logger.error("[Congress] Report fetch at offset %d failed: %s", offset, e)
                break

        return all_reports

    def _parse_report(
        self, row: list[str], cutoff_date: datetime,
    ) -> list[dict[str, Any]]:
        """Parse a single report row and fetch individual trade details.

        Row format from the API: [first_name, last_name, _, link_html, date_received]
        """
        if len(row) < 5:
            return []

        first_name = row[0].strip()
        last_name = row[1].strip()
        link_html = row[3]
        date_received = row[4].strip()

        # Parse the filing date
        try:
            filed_date = datetime.strptime(date_received, "%m/%d/%Y")
            if filed_date < cutoff_date:
                return []
        except ValueError:
            filed_date = datetime.now()

        # Extract link from HTML
        link_soup = BeautifulSoup(link_html, "lxml")
        link_tag = link_soup.find("a")
        if not link_tag or not link_tag.get("href"):
            return []

        link = link_tag["href"]

        # Skip PDFs — we can't parse them
        if link.startswith(PDF_PREFIX):
            return []

        # Fetch the report detail page
        member_name = f"{first_name} {last_name}"
        report_url = f"{ROOT}{link}"

        time.sleep(RATE_LIMIT_SECS)
        try:
            resp = self._session.get(report_url, timeout=15)

            # Session expired — reset CSRF
            if resp.url == LANDING_PAGE_URL:
                logger.info("[Congress] Session expired, re-authenticating")
                token = self._get_csrf_token()
                if not token:
                    return []
                resp = self._session.get(report_url, timeout=15)

        except Exception as e:
            logger.debug("[Congress] Report detail fetch failed: %s", e)
            return []

        # Parse trade rows from the detail page
        soup = BeautifulSoup(resp.text, "lxml")
        tbodies = soup.find_all("tbody")
        if not tbodies:
            return []

        trades: list[dict[str, Any]] = []
        for tr in tbodies[0].find_all("tr"):
            cols = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cols) < 7:
                continue

            # Columns: [#, tx_date, _, ticker, asset_name, asset_type, order_type, amount, ...]
            tx_date_str = cols[1] if len(cols) > 1 else ""
            ticker = cols[3] if len(cols) > 3 else ""
            asset_name = cols[4] if len(cols) > 4 else ""
            asset_type = cols[5] if len(cols) > 5 else ""
            order_type = cols[6] if len(cols) > 6 else ""
            amount_range = cols[7] if len(cols) > 7 else ""

            # Only track stocks (skip options, bonds, etc. unless they have a ticker)
            ticker = ticker.strip().replace("--", "").strip()
            if not ticker and asset_type != "Stock":
                continue

            # Parse transaction date
            try:
                tx_date = datetime.strptime(tx_date_str, "%m/%d/%Y").date()
            except ValueError:
                tx_date = filed_date.date()

            trade_id = hashlib.md5(
                f"{member_name}_{ticker}_{tx_date}_{order_type}".encode()
            ).hexdigest()[:16]

            trades.append({
                "id": trade_id,
                "member_name": member_name,
                "chamber": "senate",
                "ticker": ticker,
                "asset_name": asset_name,
                "tx_type": order_type,
                "tx_date": tx_date,
                "filed_date": filed_date.date(),
                "amount_range": amount_range,
                "source_url": report_url,
            })

        return trades

    # ── Private: DB persistence ──────────────────────────────────────

    def _save_trades(self, db: Any, trades: list[dict[str, Any]]) -> None:
        """Persist congressional trades to DuckDB."""
        for trade in trades:
            try:
                db.execute(
                    """
                    INSERT INTO congressional_trades
                        (id, member_name, chamber, ticker, asset_name,
                         tx_type, tx_date, filed_date, amount_range, source_url)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    [
                        trade["id"],
                        trade["member_name"],
                        trade["chamber"],
                        trade["ticker"],
                        trade["asset_name"],
                        trade["tx_type"],
                        trade["tx_date"],
                        trade["filed_date"],
                        trade["amount_range"],
                        trade["source_url"],
                    ],
                )
            except Exception as e:
                logger.debug("[Congress] Insert failed for trade %s: %s", trade["id"], e)

    def _tickers_from_db(self) -> list[ScoredTicker]:
        """Build ScoredTicker list from recent congressional trades in DB."""
        db = get_db()

        # Count trades per ticker, distinguishing buys and sells
        rows = db.execute(
            """
            SELECT ticker,
                   COUNT(*) as trade_count,
                   COUNT(DISTINCT member_name) as member_count,
                   SUM(CASE WHEN tx_type LIKE '%Purchase%' THEN 1 ELSE 0 END) as buys,
                   SUM(CASE WHEN tx_type LIKE '%Sale%' THEN 1 ELSE 0 END) as sells
            FROM congressional_trades
            WHERE ticker != '' AND ticker IS NOT NULL
              AND tx_date >= CURRENT_DATE - INTERVAL '90 days'
            GROUP BY ticker
            ORDER BY trade_count DESC
            LIMIT 50
            """,
        ).fetchall()

        tickers: list[ScoredTicker] = []
        for ticker, trade_count, member_count, buys, sells in rows:
            # Score: weighted by trade count, members, and buy/sell ratio
            buy_ratio = buys / max(buys + sells, 1)
            score = float(trade_count) * 1.5 + float(member_count) * 1.0

            if buy_ratio > 0.6:
                sentiment = "bullish"
            elif buy_ratio < 0.4:
                sentiment = "bearish"
            else:
                sentiment = "neutral"

            tickers.append(
                ScoredTicker(
                    ticker=ticker,
                    discovery_score=score,
                    source="congress",
                    source_detail=(
                        f"{member_count} members, "
                        f"{buys} buys / {sells} sells"
                    ),
                    sentiment_hint=sentiment,
                    context_snippets=[
                        f"Traded by {member_count} congress members "
                        f"in last 90 days ({buys} buys, {sells} sells)"
                    ],
                )
            )

        logger.info("[Congress] Generated %d scored tickers from DB", len(tickers))
        return tickers
