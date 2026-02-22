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

import asyncio
import re
import time
import warnings
from datetime import datetime
from typing import Any

import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

from app.config import settings
from app.database import get_db
from app.models.discovery import ScoredTicker
from app.utils.logger import logger

# Suppress XML-parsed-as-HTML warnings from BeautifulSoup
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# SEC requires a descriptive User-Agent header
SEC_BASE_URL = "https://data.sec.gov"
SEC_ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data"
RATE_LIMIT_SECS = 0.15  # 6-7 req/sec, well within 10/s limit
MAX_HOLDINGS_PER_FILER = 500  # Cap to prevent massive saves (Elliott=34K+)
PER_FILER_TIMEOUT_SECS = 60  # Skip filers that take too long

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
        self._session.headers.update(
            {
                "User-Agent": settings.SEC_USER_AGENT,
                "Accept-Encoding": "gzip, deflate",
            }
        )

    # ── Public: Discovery integration ────────────────────────────────

    async def collect_recent_holdings(self) -> list[ScoredTicker]:
        """Scrape recent 13F holdings and return tickers as ScoredTicker.

        This is called during the Discovery phase. Returns unique tickers
        from the most recent filings, scored by how many institutions hold them.

        IMPORTANT: All scraping runs in a thread executor to avoid blocking
        the asyncio event loop (scraping uses synchronous requests).
        """
        db = get_db()

        # Daily guard: skip if we already scraped today
        row = db.execute(
            "SELECT COUNT(*) FROM sec_13f_holdings WHERE collected_at >= CURRENT_DATE"
        ).fetchone()
        if row and row[0] > 0:
            logger.info(
                "[SEC 13F] Already collected today (%d rows), using cache", row[0]
            )
            return self._tickers_from_db()

        # Run the synchronous scraping in a thread so we don't block the event loop
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._scrape_all_filers, db)

        return self._tickers_from_db()

    def _scrape_all_filers(self, db: Any) -> None:
        """Synchronous method that scrapes all filers (runs in thread executor)."""
        logger.info(
            "[SEC 13F] Starting 13F collection for %d filers", len(DEFAULT_FILERS)
        )

        # Ensure filers are in the DB
        self._ensure_filers(db)

        # Get active filers
        filers = db.execute(
            "SELECT cik, filer_name FROM sec_13f_filers WHERE is_active = TRUE"
        ).fetchall()

        total_holdings = 0
        for cik, name in filers:
            t0 = time.time()
            try:
                count = self._scrape_filer(db, cik, name)
                total_holdings += count
                elapsed = time.time() - t0
                if elapsed > PER_FILER_TIMEOUT_SECS:
                    logger.warning(
                        "[SEC 13F] %s took %.1fs — remaining filers may be skipped",
                        name,
                        elapsed,
                    )
            except Exception as e:
                logger.error("[SEC 13F] Failed to scrape %s (%s): %s", name, cik, e)

        logger.info(
            "[SEC 13F] Collection complete: %d total holdings saved", total_holdings
        )

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
            logger.info(
                "[SEC 13F] %s Q%s already in DB (%d holdings)",
                name,
                quarter,
                existing[0],
            )
            return 0

        # Fetch and parse the information table
        holdings = self._get_holdings(filing, cik)
        if not holdings:
            logger.warning("[SEC 13F] No holdings parsed for %s", name)
            return 0

        # Filter out holdings with no ticker resolved
        holdings = [h for h in holdings if h.get("ticker")]

        # Cap holdings per filer to prevent massive saves
        if len(holdings) > MAX_HOLDINGS_PER_FILER:
            logger.info(
                "[SEC 13F] Capping %s from %d to %d holdings (by value)",
                name,
                len(holdings),
                MAX_HOLDINGS_PER_FILER,
            )
            holdings.sort(key=lambda h: h.get("value_usd", 0), reverse=True)
            holdings = holdings[:MAX_HOLDINGS_PER_FILER]

        # Persist
        saved = 0
        for h in holdings:
            try:
                db.execute(
                    """
                    INSERT INTO sec_13f_holdings
                        (cik, ticker, name_of_issuer, cusip, value_usd,
                         shares, share_type, filing_quarter, filing_date,
                         collected_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NOW())
                    ON CONFLICT (cik, ticker, filing_quarter) DO UPDATE SET
                        value_usd = EXCLUDED.value_usd,
                        shares = EXCLUDED.shares,
                        collected_at = EXCLUDED.collected_at
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

        logger.info(
            "[SEC 13F] Saved %d/%d holdings for %s (%s)",
            saved,
            len(holdings),
            name,
            quarter,
        )
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
            logger.warning(
                "[SEC 13F] Submissions %s returned %d", url, resp.status_code
            )
        except Exception as e:
            logger.error("[SEC 13F] Submissions request failed: %s", e)
        return None

    def _find_latest_13f(
        self,
        submissions: dict[str, Any],
        cik: str,
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
                    f"{SEC_ARCHIVES_URL}/{stripped_cik}/{file_accession}/{primary_doc}"
                ),
                "cik": stripped_cik,
                "file_accession": file_accession,
            }

        return None

    def _get_holdings(
        self,
        filing: dict[str, Any],
        cik: str,
    ) -> list[dict[str, Any]]:
        """Fetch and parse holdings from a 13F filing's information table.

        Uses the EDGAR index.json API to find the info-table XML file,
        which is typically the non-primary_doc.xml XML file in the filing.
        """
        stripped_cik = cik.lstrip("0")
        file_accession = filing["file_accession"]

        # ── Strategy 1: Use index.json to find the info table XML ──
        index_json_url = (
            f"{SEC_ARCHIVES_URL}/{stripped_cik}/{file_accession}/index.json"
        )
        time.sleep(RATE_LIMIT_SECS)
        info_table_url = None
        try:
            resp = self._session.get(index_json_url, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("directory", {}).get("item", [])
                # The info table is the XML file that ISN'T primary_doc.xml
                xml_files = [
                    item["name"]
                    for item in items
                    if item.get("name", "").endswith(".xml")
                    and item.get("name") != "primary_doc.xml"
                ]
                if xml_files:
                    # Pick the first non-primary XML (info table)
                    info_table_url = (
                        f"{SEC_ARCHIVES_URL}/{stripped_cik}/"
                        f"{file_accession}/{xml_files[0]}"
                    )
                    logger.debug(
                        "[SEC 13F] Found info table via JSON index: %s",
                        xml_files[0],
                    )
        except Exception as e:
            logger.warning("[SEC 13F] index.json fetch failed: %s", e)

        # ── Strategy 2: Scrape the index HTML page for links ──
        if not info_table_url:
            index_url = filing["index_url"]
            time.sleep(RATE_LIMIT_SECS)
            try:
                resp = self._session.get(index_url, timeout=15)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "lxml")
                    for link in soup.find_all("a", href=True):
                        href = link["href"]
                        text = link.get_text(strip=True).lower()
                        # Match known info table patterns
                        if any(
                            x in text or x in href.lower()
                            for x in (
                                "infotable",
                                "information table",
                                "informationtable",
                            )
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

                    # Fallback: grab any XML that isn't primary_doc.xml
                    if not info_table_url:
                        for link in soup.find_all("a", href=True):
                            href = link["href"]
                            fname = href.split("/")[-1] if "/" in href else href
                            if (
                                fname.endswith(".xml")
                                and fname != "primary_doc.xml"
                                and not fname.startswith("R")
                            ):
                                if href.startswith("/"):
                                    info_table_url = f"https://www.sec.gov{href}"
                                elif href.startswith("http"):
                                    info_table_url = href
                                else:
                                    info_table_url = (
                                        f"{SEC_ARCHIVES_URL}/{stripped_cik}/"
                                        f"{file_accession}/{fname}"
                                    )
                                logger.debug(
                                    "[SEC 13F] Found info table via HTML fallback: %s",
                                    fname,
                                )
                                break
            except Exception as e:
                logger.error("[SEC 13F] Index HTML fetch failed: %s", e)

        if not info_table_url:
            logger.warning(
                "[SEC 13F] No info table found for %s",
                filing["accession"],
            )
            return []

        # Fetch the info table XML
        time.sleep(RATE_LIMIT_SECS)
        try:
            resp = self._session.get(info_table_url, timeout=30)
            if resp.status_code != 200:
                logger.warning(
                    "[SEC 13F] Info table fetch returned %d: %s",
                    resp.status_code,
                    info_table_url,
                )
                return []
        except Exception as e:
            logger.error("[SEC 13F] Info table fetch failed: %s", e)
            return []

        return self._parse_info_table(resp.text)

    def _parse_info_table(self, content: str) -> list[dict[str, Any]]:
        """Parse a 13F information table (XML) into holdings dicts.

        Uses the proper XML parser to handle SEC infoTable entries.
        """
        holdings: list[dict[str, Any]] = []

        # ── Try with proper XML parser first ──
        try:
            soup = BeautifulSoup(content, "xml")
            info_entries = soup.find_all("infoTable")
            if info_entries:
                for entry in info_entries:
                    holding = self._parse_xml_entry(entry)
                    if holding and holding.get("ticker"):
                        holdings.append(holding)
                if holdings:
                    logger.info(
                        "[SEC 13F] XML parser found %d holdings",
                        len(holdings),
                    )
                    return holdings
        except Exception as e:
            logger.debug("[SEC 13F] XML parser failed: %s", e)

        # ── Fallback: HTML parser with regex tag matching ──
        soup = BeautifulSoup(content, "lxml")
        info_entries = soup.find_all(re.compile(r"infotable", re.IGNORECASE))
        if info_entries:
            for entry in info_entries:
                holding = self._parse_xml_entry(entry)
                if holding and holding.get("ticker"):
                    holdings.append(holding)
            return holdings

        # ── Fallback: HTML table format ──
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
        """Best-effort CUSIP/name -> ticker symbol resolution.

        Strategy:
        1. Check hardcoded CUSIP map (most common large-cap)
        2. Check company name map (well-known names)
        3. Use yfinance CUSIP lookup as dynamic fallback
        """
        # Well-known CUSIP -> ticker mapping (top holdings)
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
            # Additional frequently held stocks
            "02005N100": "ALLY",
            "172967424": "C",
            "084670702": "BRK-B",
            "78462F103": "SPY",
            "464287655": "IWM",
            "808513105": "SCHW",
            "369604103": "GE",
            "459200101": "IBM",
            "31620M106": "FANG",
            "48203R104": "JNPR",
            "585055106": "MDT",
            "571903202": "MA",
            "00206R102": "T",
            "92343V104": "VZ",
            "12504L109": "CSIQ",
            "002824100": "ABT",
            "026874784": "AIG",
            "00287Y109": "ABBV",
            "718172109": "PFE",
            "68389X105": "ORCL",
            "11135F101": "CRM",
            "64110L106": "NFLX",
            "007903107": "AMD",
            "458140100": "INTC",
            "747525103": "QCOM",
            "70450Y103": "PYPL",
            "191216100": "KO",
            "713448108": "PEP",
            "166764100": "CVX",
            "30231G102": "XOM",
        }

        clean_cusip = cusip.strip()
        if clean_cusip in cusip_map:
            return cusip_map[clean_cusip]

        # Try to extract from issuer name (e.g., "APPLE INC" -> search via heuristics)
        name_map: dict[str, str] = {
            "APPLE": "AAPL",
            "MICROSOFT": "MSFT",
            "AMAZON": "AMZN",
            "ALPHABET": "GOOGL",
            "GOOGLE": "GOOGL",
            "META PLATFORMS": "META",
            "FACEBOOK": "META",
            "NVIDIA": "NVDA",
            "TESLA": "TSLA",
            "BERKSHIRE": "BRK-B",
            "JPMORGAN": "JPM",
            "JOHNSON": "JNJ",
            "UNITEDHEALTH": "UNH",
            "VISA": "V",
            "PROCTER": "PG",
            "ELI LILLY": "LLY",
            "MASTERCARD": "MA",
            "WALMART": "WMT",
            "BROADCOM": "AVGO",
            "COSTCO": "COST",
            "CISCO": "CSCO",
            "ABBVIE": "ABBV",
            "PFIZER": "PFE",
            "ORACLE": "ORCL",
            "SALESFORCE": "CRM",
            "NETFLIX": "NFLX",
            "ADOBE": "ADBE",
            "AMD": "AMD",
            "INTEL": "INTC",
            "QUALCOMM": "QCOM",
            "PAYPAL": "PYPL",
            "BOEING": "BA",
            "DISNEY": "DIS",
            "COCA-COLA": "KO",
            "PEPSICO": "PEP",
            "MERCK": "MRK",
            "CHEVRON": "CVX",
            "EXXON": "XOM",
            "ALLY": "ALLY",
            "GENERAL ELECTRIC": "GE",
            "GENERAL MOTORS": "GM",
            "CITIGROUP": "C",
            "BANK OF AMERICA": "BAC",
            "WELLS FARGO": "WFC",
            "GOLDMAN": "GS",
            "MORGAN STANLEY": "MS",
            "COCA COLA": "KO",
            "HOME DEPOT": "HD",
            "MCDONALD": "MCD",
            "NIKE": "NKE",
            "STARBUCKS": "SBUX",
            "UBER": "UBER",
            "AIRBNB": "ABNB",
            "SNOWFLAKE": "SNOW",
            "PALANTIR": "PLTR",
            "CROWDSTRIKE": "CRWD",
            "DATADOG": "DDOG",
            "SERVICENOW": "NOW",
            "SHOPIFY": "SHOP",
            "ADVANCED MICRO": "AMD",
            "TAIWAN SEMI": "TSM",
            "ASML": "ASML",
            "CATERPILLAR": "CAT",
            "DEERE": "DE",
            "LOCKHEED": "LMT",
            "RAYTHEON": "RTX",
            "AMERICAN EXPRESS": "AXP",
            "CAPITAL ONE": "COF",
            "T-MOBILE": "TMUS",
            "VERIZON": "VZ",
            "AT&T": "T",
            "COMCAST": "CMCSA",
            "TARGET": "TGT",
            "FEDEX": "FDX",
            "SCHWAB": "SCHW",
            "BLACKROCK": "BLK",
        }

        upper_name = name.upper()
        for pattern, tick in name_map.items():
            if pattern in upper_name:
                return tick

        # NOTE: yfinance fallback removed — it was making thousands of slow
        # network calls per filer (e.g., Elliott has 34K holdings). The
        # hardcoded CUSIP + name maps cover all major stocks. Unknown
        # CUSIPs return empty string and are filtered out.
        return ""

    @staticmethod
    def _name_to_ticker_yf(name: str) -> str:
        """Try to resolve company name to ticker via yfinance search."""
        try:
            import yfinance as yf

            # Simplify name for search (remove "INC", "CORP", etc.)
            search_name = (
                name.upper()
                .replace(" INC", "")
                .replace(" CORP", "")
                .replace(" LTD", "")
                .replace(" LLC", "")
                .replace(" CO", "")
                .replace("  ", " ")
                .strip()
            )
            if len(search_name) < 3:
                return ""
            # Use yfinance search
            results = yf.Search(search_name)
            if hasattr(results, "quotes") and results.quotes:
                # Return the first match's symbol
                return results.quotes[0].get("symbol", "")
        except Exception:
            pass
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
