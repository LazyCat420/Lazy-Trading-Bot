"""SEC 13F Filings Collector — scrapes institutional holdings from SEC EDGAR.

Uses the SEC EDGAR submissions API (data.sec.gov) to retrieve 13F-HR filings
for well-known institutional filers (hedge funds, mutual funds) and extract
their equity holdings.

Data source:
    https://data.sec.gov/submissions/CIK{cik}.json  (submissions index)
    https://www.sec.gov/Archives/edgar/data/{cik}/...  (filing documents)

Rate limit: SEC requests max 10 req/sec. We use 0.15s between requests.
Auth: User-Agent header only (required by SEC).
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any

import requests
from bs4 import BeautifulSoup

from app.config import settings
from app.database import get_db
from app.models.discovery import ScoredTicker
from app.utils.logger import logger

# SEC requires a descriptive User-Agent header
SEC_BASE_URL = "https://data.sec.gov"
SEC_ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data"
RATE_LIMIT_SECS = 0.15  # 6–7 req/sec, well within 10/s limit

# ── Default watchlist of major institutional filers ─────────────────
# CIK numbers for well-known hedge funds / institutional investors.
# Users can extend this via the sec_13f_filers DB table.
DEFAULT_FILERS: list[dict[str, str]] = [
    {"cik": "0001067983", "name": "Berkshire Hathaway"},
    {"cik": "0001350694", "name": "Citadel Advisors"},
    {"cik": "0001037389", "name": "Renaissance Technologies"},
    {"cik": "0001336528", "name": "Bridgewater Associates"},
    {"cik": "0001364742", "name": "Elliott Investment Management"},
    {"cik": "0001061768", "name": "Two Sigma Investments"},
    {"cik": "0001649339", "name": "Point72 Asset Management"},
    {"cik": "0001167483", "name": "DE Shaw & Co"},
    {"cik": "0001040127", "name": "AQR Capital Management"},
    {"cik": "0001009207", "name": "Millennium Management"},
    {"cik": "0001116304", "name": "Pershing Square Capital"},
    {"cik": "0001079114", "name": "Viking Global Investors"},
    {"cik": "0001029160", "name": "Druckenmiller (Duquesne Family Office)"},
    {"cik": "0001541617", "name": "Coatue Management"},
    {"cik": "0001599901", "name": "Tiger Global Management"},
]


class SEC13FCollector:
    """Collects 13F-HR institutional holdings from SEC EDGAR."""

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": settings.SEC_USER_AGENT,
            "Accept-Encoding": "gzip, deflate",
        })

    # ── Public: Discovery integration ────────────────────────────────

    async def collect_recent_holdings(self) -> list[ScoredTicker]:
        """Scrape recent 13F holdings and return tickers as ScoredTicker.

        This is called during the Discovery phase. Returns unique tickers
        from the most recent filings, scored by how many institutions hold them.
        """
        db = get_db()

        # Daily guard: skip if we already scraped today
        row = db.execute(
            "SELECT COUNT(*) FROM sec_13f_holdings "
            "WHERE collected_at >= CURRENT_DATE"
        ).fetchone()
        if row and row[0] > 0:
            logger.info("[SEC 13F] Already collected today (%d rows), using cache", row[0])
            return self._tickers_from_db()

        logger.info("[SEC 13F] Starting 13F collection for %d filers", len(DEFAULT_FILERS))

        # Ensure filers are in the DB
        self._ensure_filers(db)

        # Get active filers
        filers = db.execute(
            "SELECT cik, filer_name FROM sec_13f_filers WHERE is_active = TRUE"
        ).fetchall()

        total_holdings = 0
        for cik, name in filers:
            try:
                count = self._scrape_filer(db, cik, name)
                total_holdings += count
            except Exception as e:
                logger.error("[SEC 13F] Failed to scrape %s (%s): %s", name, cik, e)

        logger.info("[SEC 13F] Collection complete: %d total holdings saved", total_holdings)
        return self._tickers_from_db()

    async def get_holdings_for_ticker(self, ticker: str) -> list[dict[str, Any]]:
        """Get institutional holders for a specific ticker (pipeline step).

        Returns list of dicts with filer info and position details.
        """
        db = get_db()
        rows = db.execute(
            """
            SELECT h.cik, f.filer_name, h.value_usd, h.shares,
                   h.share_type, h.filing_quarter, h.filing_date
            FROM sec_13f_holdings h
            LEFT JOIN sec_13f_filers f ON h.cik = f.cik
            WHERE h.ticker = ?
            ORDER BY h.value_usd DESC
            """,
            [ticker],
        ).fetchall()

        return [
            {
                "cik": r[0],
                "filer_name": r[1],
                "value_usd": r[2],
                "shares": r[3],
                "share_type": r[4],
                "filing_quarter": r[5],
                "filing_date": str(r[6]) if r[6] else None,
            }
            for r in rows
        ]

    # ── Private: scraping logic ──────────────────────────────────────

    def _ensure_filers(self, db: Any) -> None:
        """Seed default filers into DB if not present."""
        for filer in DEFAULT_FILERS:
            try:
                db.execute(
                    """
                    INSERT INTO sec_13f_filers (cik, filer_name)
                    VALUES (?, ?)
                    ON CONFLICT (cik) DO NOTHING
                    """,
                    [filer["cik"], filer["name"]],
                )
            except Exception:
                pass  # Already exists

    def _scrape_filer(self, db: Any, cik: str, name: str) -> int:
        """Scrape 13F-HR for a single filer. Returns number of holdings saved."""
        logger.info("[SEC 13F] Scraping %s (CIK: %s)", name, cik)

        # Get submissions index
        submissions = self._get_submissions(cik)
        if not submissions:
            return 0

        # Find latest 13F-HR filing
        filing = self._find_latest_13f(submissions, cik)
        if not filing:
            logger.info("[SEC 13F] No 13F-HR found for %s", name)
            return 0

        quarter = filing["quarter"]
        filing_date = filing["filing_date"]

        # Check if we already have this quarter's data
        existing = db.execute(
            "SELECT COUNT(*) FROM sec_13f_holdings WHERE cik = ? AND filing_quarter = ?",
            [cik, quarter],
        ).fetchone()
        if existing and existing[0] > 0:
            logger.info("[SEC 13F] %s Q%s already in DB (%d holdings)", name, quarter, existing[0])
            return 0

        # Fetch and parse the information table
        holdings = self._get_holdings(filing, cik)
        if not holdings:
            logger.warning("[SEC 13F] No holdings parsed for %s", name)
            return 0

        # Persist
        saved = 0
        for h in holdings:
            try:
                db.execute(
                    """
                    INSERT INTO sec_13f_holdings
                        (cik, ticker, name_of_issuer, cusip, value_usd,
                         shares, share_type, filing_quarter, filing_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (cik, ticker, filing_quarter) DO UPDATE SET
                        value_usd = EXCLUDED.value_usd,
                        shares = EXCLUDED.shares,
                        collected_at = CURRENT_TIMESTAMP
                    """,
                    [
                        cik,
                        h.get("ticker", ""),
                        h.get("name_of_issuer", ""),
                        h.get("cusip", ""),
                        h.get("value_usd", 0),
                        h.get("shares", 0),
                        h.get("share_type", "SH"),
                        quarter,
                        filing_date,
                    ],
                )
                saved += 1
            except Exception as e:
                logger.debug("[SEC 13F] Insert failed for %s: %s", h.get("ticker"), e)

        # Update filer last_checked
        db.execute(
            "UPDATE sec_13f_filers SET last_checked = CURRENT_TIMESTAMP WHERE cik = ?",
            [cik],
        )

        logger.info("[SEC 13F] Saved %d/%d holdings for %s (%s)", saved, len(holdings), name, quarter)
        return saved

    def _get_submissions(self, cik: str) -> dict[str, Any] | None:
        """Fetch company submissions JSON from SEC EDGAR."""
        # Pad CIK to 10 digits
        padded_cik = cik.lstrip("0").zfill(10)
        url = f"{SEC_BASE_URL}/submissions/CIK{padded_cik}.json"

        time.sleep(RATE_LIMIT_SECS)
        try:
            resp = self._session.get(url, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            logger.warning("[SEC 13F] Submissions %s returned %d", url, resp.status_code)
        except Exception as e:
            logger.error("[SEC 13F] Submissions request failed: %s", e)
        return None

    def _find_latest_13f(
        self, submissions: dict[str, Any], cik: str,
    ) -> dict[str, Any] | None:
        """Find the most recent 13F-HR filing from the submissions data."""
        recent = submissions.get("filings", {}).get("recent", {})
        if not recent:
            return None

        forms = recent.get("form", [])
        filing_dates = recent.get("filingDate", [])
        accession_numbers = recent.get("accessionNumber", [])
        primary_docs = recent.get("primaryDocument", [])

        for i in range(len(forms)):
            if forms[i] not in ("13F-HR", "13F-HR/A"):
                continue
            if i >= len(filing_dates) or i >= len(accession_numbers):
                continue

            filing_date_str = filing_dates[i]
            accession = accession_numbers[i]
            primary_doc = primary_docs[i] if i < len(primary_docs) else ""

            # Determine the quarter the filing covers
            try:
                dt = datetime.strptime(filing_date_str, "%Y-%m-%d")
                # 13F filings filed in Q1 cover Q4 of prior year, etc.
                if dt.month <= 3:
                    q_year, q_num = dt.year - 1, 4
                elif dt.month <= 6:
                    q_year, q_num = dt.year, 1
                elif dt.month <= 9:
                    q_year, q_num = dt.year, 2
                else:
                    q_year, q_num = dt.year, 3
            except ValueError:
                continue

            file_accession = accession.replace("-", "")
            stripped_cik = cik.lstrip("0")

            return {
                "accession": accession,
                "filing_date": filing_date_str,
                "quarter": f"{q_year}Q{q_num}",
                "primary_doc": primary_doc,
                "index_url": (
                    f"{SEC_ARCHIVES_URL}/{stripped_cik}/{file_accession}/"
                    f"{accession}-index.htm"
                ),
                "filing_url": (
                    f"{SEC_ARCHIVES_URL}/{stripped_cik}/{file_accession}/"
                    f"{primary_doc}"
                ),
                "cik": stripped_cik,
                "file_accession": file_accession,
            }

        return None

    def _get_holdings(
        self, filing: dict[str, Any], cik: str,
    ) -> list[dict[str, Any]]:
        """Fetch and parse holdings from a 13F filing's information table."""
        stripped_cik = cik.lstrip("0")
        file_accession = filing["file_accession"]

        # First, get the filing index to find the info table XML
        index_url = filing["index_url"]
        time.sleep(RATE_LIMIT_SECS)
        try:
            resp = self._session.get(index_url, timeout=15)
            if resp.status_code != 200:
                logger.warning("[SEC 13F] Index page %d for %s", resp.status_code, index_url)
                return []
        except Exception as e:
            logger.error("[SEC 13F] Index fetch failed: %s", e)
            return []

        # Find the information table document (XML or HTML)
        soup = BeautifulSoup(resp.text, "lxml")
        info_table_url = None

        for link in soup.find_all("a", href=True):
            href = link["href"]
            text = link.get_text(strip=True).lower()
            # Look for common info table filenames
            if any(
                x in text or x in href.lower()
                for x in ("infotable", "information table", "informationtable")
            ):
                if href.startswith("/"):
                    info_table_url = f"https://www.sec.gov{href}"
                elif href.startswith("http"):
                    info_table_url = href
                else:
                    info_table_url = (
                        f"{SEC_ARCHIVES_URL}/{stripped_cik}/"
                        f"{file_accession}/{href}"
                    )
                break

        if not info_table_url:
            # Fallback: try common filename patterns
            for fname in ("infotable.xml", "informationtable.xml", "primary_doc.xml"):
                fallback_url = (
                    f"{SEC_ARCHIVES_URL}/{stripped_cik}/"
                    f"{file_accession}/{fname}"
                )
                time.sleep(RATE_LIMIT_SECS)
                try:
                    r = self._session.head(fallback_url, timeout=10)
                    if r.status_code == 200:
                        info_table_url = fallback_url
                        break
                except Exception:
                    pass

        if not info_table_url:
            logger.warning("[SEC 13F] No info table found for %s", filing["accession"])
            return []

        # Fetch the info table
        time.sleep(RATE_LIMIT_SECS)
        try:
            resp = self._session.get(info_table_url, timeout=30)
            if resp.status_code != 200:
                return []
        except Exception as e:
            logger.error("[SEC 13F] Info table fetch failed: %s", e)
            return []

        return self._parse_info_table(resp.text)

    def _parse_info_table(self, content: str) -> list[dict[str, Any]]:
        """Parse a 13F information table (XML or HTML) into holdings dicts."""
        holdings: list[dict[str, Any]] = []
        soup = BeautifulSoup(content, "lxml")

        # Try XML format first (most common for 13F)
        info_entries = soup.find_all(re.compile(r"infotable", re.IGNORECASE))
        if info_entries:
            for entry in info_entries:
                holding = self._parse_xml_entry(entry)
                if holding and holding.get("ticker"):
                    holdings.append(holding)
            return holdings

        # Fallback: try HTML table format
        rows = soup.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) >= 4:
                holding = self._parse_html_row(cells)
                if holding and holding.get("ticker"):
                    holdings.append(holding)

        return holdings

    def _parse_xml_entry(self, entry: Any) -> dict[str, Any] | None:
        """Parse a single <infoTable> XML entry."""
        def _get_text(tag_name: str) -> str:
            tag = entry.find(re.compile(tag_name, re.IGNORECASE))
            return tag.get_text(strip=True) if tag else ""

        name = _get_text("nameofissuer")
        cusip = _get_text("cusip")
        value_str = _get_text("value")
        shares_str = _get_text(r"sshprnamt$")
        share_type = _get_text("sshprnamttype")
        title = _get_text("titleofclass")

        # Try to extract a ticker from the title of class or name
        ticker = self._cusip_to_ticker(cusip, name, title)

        try:
            value_usd = float(value_str.replace(",", "")) if value_str else 0
        except ValueError:
            value_usd = 0

        try:
            shares = int(shares_str.replace(",", "")) if shares_str else 0
        except ValueError:
            shares = 0

        if not name:
            return None

        return {
            "name_of_issuer": name,
            "cusip": cusip,
            "value_usd": value_usd,  # in thousands
            "shares": shares,
            "share_type": share_type or "SH",
            "ticker": ticker,
        }

    def _parse_html_row(self, cells: list[Any]) -> dict[str, Any] | None:
        """Parse a holdings row from an HTML table."""
        try:
            name = cells[0].get_text(strip=True)
            title = cells[1].get_text(strip=True) if len(cells) > 1 else ""
            cusip = cells[2].get_text(strip=True) if len(cells) > 2 else ""
            value_str = cells[3].get_text(strip=True) if len(cells) > 3 else "0"
            shares_str = cells[4].get_text(strip=True) if len(cells) > 4 else "0"
            share_type = cells[5].get_text(strip=True) if len(cells) > 5 else "SH"

            ticker = self._cusip_to_ticker(cusip, name, title)

            value_usd = float(value_str.replace(",", "")) if value_str else 0
            shares = int(shares_str.replace(",", "")) if shares_str else 0

            return {
                "name_of_issuer": name,
                "cusip": cusip,
                "value_usd": value_usd,
                "shares": shares,
                "share_type": share_type,
                "ticker": ticker,
            }
        except (ValueError, IndexError):
            return None

    def _cusip_to_ticker(self, cusip: str, name: str, title: str) -> str:
        """Best-effort CUSIP/name → ticker symbol resolution.

        Uses a built-in mapping of well-known CUSIPs and falls back
        to extracting ticker patterns from the issuer name.
        """
        # Well-known CUSIP → ticker mapping (top holdings)
        cusip_map: dict[str, str] = {
            "594918104": "MSFT",
            "037833100": "AAPL",
            "02079K305": "GOOG",
            "02079K107": "GOOGL",
            "023135106": "AMZN",
            "67066G104": "NVDA",
            "30303M102": "META",
            "88160R101": "TSLA",
            "46625H100": "JPM",
            "92826C839": "V",
            "91324P102": "UNH",
            "17275R102": "CSCO",
            "478160104": "JNJ",
            "00724F101": "ADBE",
            "532457108": "LLY",
            "742718109": "PG",
            "931142103": "WMT",
            "58933Y105": "MRK",
            "20030N101": "CMCSA",
            "87612E106": "TGT",
            "22160K105": "COST",
            "31428X106": "FDX",
            "254687106": "DIS",
            "260557103": "DOW",
            "111320107": "BA",
            "09247X101": "BLK",
        }

        clean_cusip = cusip.strip()
        if clean_cusip in cusip_map:
            return cusip_map[clean_cusip]

        # Try to extract from issuer name (e.g., "APPLE INC" → search via heuristics)
        # Use common company name → ticker shortcuts
        name_map: dict[str, str] = {
            "APPLE": "AAPL", "MICROSOFT": "MSFT", "AMAZON": "AMZN",
            "ALPHABET": "GOOGL", "GOOGLE": "GOOGL", "META PLATFORMS": "META",
            "FACEBOOK": "META", "NVIDIA": "NVDA", "TESLA": "TSLA",
            "BERKSHIRE": "BRK-B", "JPMORGAN": "JPM", "JOHNSON": "JNJ",
            "UNITEDHEALTH": "UNH", "VISA": "V", "PROCTER": "PG",
            "ELI LILLY": "LLY", "MASTERCARD": "MA", "WALMART": "WMT",
            "BROADCOM": "AVGO", "COSTCO": "COST", "CISCO": "CSCO",
            "ABBVIE": "ABBV", "PFIZER": "PFE", "ORACLE": "ORCL",
            "SALESFORCE": "CRM", "NETFLIX": "NFLX", "ADOBE": "ADBE",
            "AMD": "AMD", "INTEL": "INTC", "QUALCOMM": "QCOM",
            "PAYPAL": "PYPL", "BOEING": "BA", "DISNEY": "DIS",
            "COCA-COLA": "KO", "PEPSICO": "PEP", "MERCK": "MRK",
            "CHEVRON": "CVX", "EXXON": "XOM",
        }

        upper_name = name.upper()
        for pattern, tick in name_map.items():
            if pattern in upper_name:
                return tick

        # Last resort: return empty (will be filtered out)
        return ""

    # ── Private: DB queries ──────────────────────────────────────────

    def _tickers_from_db(self) -> list[ScoredTicker]:
        """Build ScoredTicker list from recent 13F holdings in DB."""
        db = get_db()

        # Count how many institutions hold each ticker
        rows = db.execute(
            """
            SELECT ticker, COUNT(DISTINCT cik) as inst_count,
                   SUM(value_usd) as total_value
            FROM sec_13f_holdings
            WHERE ticker != '' AND ticker IS NOT NULL
            GROUP BY ticker
            ORDER BY inst_count DESC
            LIMIT 50
            """,
        ).fetchall()

        tickers: list[ScoredTicker] = []
        for ticker, inst_count, total_value in rows:
            # Score: number of institutions × 2.0 (heavy signal)
            score = float(inst_count) * 2.0
            tickers.append(
                ScoredTicker(
                    ticker=ticker,
                    discovery_score=score,
                    source="sec_13f",
                    source_detail=f"{inst_count} institutions, ${total_value:,.0f}k total",
                    sentiment_hint="bullish",  # Institutional buying is a bullish signal
                    context_snippets=[
                        f"Held by {inst_count} major institutions "
                        f"(total value: ${total_value:,.0f}k in 13F filings)"
                    ],
                )
            )

        logger.info("[SEC 13F] Generated %d scored tickers from DB", len(tickers))
        return tickers
