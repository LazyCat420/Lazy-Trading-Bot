"""Reddit Collector — scrapes financial subreddits for trending ticker mentions.

Uses Reddit's public JSON API (no auth needed). Adapted from the
RedditPurgeScraper reference implementation in example_repos/.

Pipeline:
    1. Get priority threads (stickied "Daily Discussion" etc.)
    2. Get rising/trending candidates
    3. LLM filters the most promising threads
    4. Deep scrape: title + body + top comments
    5. Extract tickers via regex, validate, and score
    6. (NEW) Stock-specific search for tickers on the scoreboard
    7. (NEW) Persist full thread data to reddit_threads table
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime
from typing import Any

import requests
from fake_useragent import UserAgent

from app.config import settings
from app.models.discovery import ScoredTicker
from app.services.llm_service import LLMService
from app.services.ticker_validator import TickerValidator
from app.utils.logger import logger


class RedditCollector:
    """Scrapes financial subreddits for trending ticker mentions."""

    # Configurable subreddit lists
    SUBREDDITS_PRIORITY = [
        "wallstreetbets",
        "stocks",
        "investing",
        "StockMarket",
        "options",
    ]
    SUBREDDITS_TRENDING = [
        "wallstreetbets",
        "pennystocks",
        "ShortSqueeze",
        "options",
        "Daytrading",
        "ValueInvesting",
        "thetagang",
        "SPACs",
    ]

    # Subreddits to search when looking for a specific stock
    SUBREDDITS_SEARCH = [
        "wallstreetbets",
        "stocks",
        "investing",
        "StockMarket",
        "options",
        "pennystocks",
        "Daytrading",
    ]

    MAX_POSTS_PER_SUB = 10  # Overridden by settings.REDDIT_MAX_POSTS_PER_SUB at runtime
    MAX_COMMENTS_PER_THREAD = 25
    MAX_THREADS_TO_SCRAPE = 15
    MAX_SEARCH_RESULTS = 5  # Per subreddit when searching for a ticker
    MAX_RETRIES = 3

    def __init__(self) -> None:
        self.validator = TickerValidator()
        self.llm = LLMService()
        self.MAX_POSTS_PER_SUB = settings.REDDIT_MAX_POSTS_PER_SUB

    def _get_headers(self) -> dict[str, str]:
        """Random user-agent to avoid Reddit blocking."""
        try:
            ua = UserAgent()
            return {"User-Agent": ua.random}
        except Exception:
            return {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/91.0.4472.124 Safari/537.36"
                )
            }

    # ── Step 1: Priority threads ────────────────────────────────────

    def get_priority_threads(self) -> list[dict[str, Any]]:
        """Get stickied/pinned 'Daily Discussion' threads."""
        threads: list[dict[str, Any]] = []
        logger.info("[Reddit] Step 1: Checking priority threads...")

        for sub in self.SUBREDDITS_PRIORITY:
            posts = self._fetch_subreddit(sub, "hot", limit=self.MAX_POSTS_PER_SUB)
            for post in posts:
                title = post.get("title", "")
                if (
                    post.get("stickied")
                    or "Daily" in title
                    or "Moves Tomorrow" in title
                    or "Discussion" in title
                ):
                    logger.info(
                        "[Reddit]   -> Priority thread: %s (r/%s)",
                        title[:60],
                        sub,
                    )
                    threads.append(post)

        logger.info("[Reddit] Found %d priority threads", len(threads))
        return threads

    # ── Step 2: Trending candidates ─────────────────────────────────

    def get_trending_candidates(self) -> list[dict[str, Any]]:
        """Get rising/new posts for fresh candidates."""
        candidates: list[dict[str, Any]] = []
        logger.info("[Reddit] Step 2: Scanning rising trends...")

        for sub in self.SUBREDDITS_TRENDING:
            posts = self._fetch_subreddit(sub, "rising", limit=self.MAX_POSTS_PER_SUB)
            candidates.extend(posts)

        logger.info("[Reddit] Found %d trending candidates", len(candidates))
        return candidates

    # ── Step 3: LLM thread filter ───────────────────────────────────

    async def filter_with_llm(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Filter threads likely discussing stock catalysts.

        Uses a fast keyword-based filter first (no LLM needed).
        Falls back to LLM only if keyword filter finds nothing.
        """
        if not candidates:
            return []

        logger.info(
            "[Reddit] Step 3: Filtering %d candidates with keywords...",
            len(candidates),
        )

        # Fast keyword filter — no LLM needed
        selected = self._keyword_filter(candidates)

        if selected:
            logger.info(
                "[Reddit]   -> Keyword filter selected %d/%d threads",
                len(selected),
                len(candidates),
            )
            return selected

        # Fallback: if keyword filter found nothing, try LLM
        logger.info("[Reddit]   -> Keyword filter found nothing, trying LLM...")
        return await self._llm_filter(candidates)

    @staticmethod
    def _keyword_filter(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Fast keyword-based thread filter (no LLM dependency).

        Selects threads that mention tickers ($AAPL style), earnings,
        options plays, or specific financial terms.
        """
        # Patterns indicating financial discussion worth scraping
        _TICKER_RE = re.compile(r"\$[A-Z]{2,5}\b")
        _FINANCE_KEYWORDS = {
            "earnings",
            "buy",
            "sell",
            "calls",
            "puts",
            "short",
            "squeeze",
            "DD",
            "due diligence",
            "yolo",
            "portfolio",
            "dividend",
            "bull",
            "bear",
            "breakout",
            "undervalued",
            "overvalued",
            "revenue",
            "guidance",
            "pe ratio",
            "eps",
            "market cap",
            "shares",
            "stock",
            "options",
            "profit",
            "p/e",
            "ipo",
            "merger",
            "acquisition",
            "catalyst",
            "mooning",
            "to the moon",
            "tendies",
            "bagholder",
            "gap up",
            "gap down",
            "price target",
            "analyst",
            "upgrade",
            "downgrade",
        }

        selected = []
        for thread in candidates:
            title = thread.get("title", "")
            body = thread.get("selftext", "")
            text = f"{title} {body}".lower()

            # Always include stickied threads
            if thread.get("stickied"):
                selected.append(thread)
                continue

            # Include if title has a ticker mention ($AAPL style)
            if _TICKER_RE.search(title):
                selected.append(thread)
                continue

            # Include if contains finance keywords
            if any(kw in text for kw in _FINANCE_KEYWORDS):
                selected.append(thread)
                continue

        return selected

    async def _llm_filter(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """LLM-based thread filter (fallback when keyword filter finds nothing)."""
        titles_text = "\n".join(
            f"{i}. {p['title']} (r/{p['subreddit']})" for i, p in enumerate(candidates)
        )

        prompt = f"""Review these Reddit thread titles. Identify the indexes of threads
that are likely discussing a specific stock ticker, earnings play, or catalyst.
Ignore generic memes, shitposts, or gain/loss porn unless a ticker is mentioned.

TITLES:
{titles_text}

Output ONLY a JSON list of indexes, e.g.: [0, 2, 5]
If none are relevant, output: []"""

        try:
            import json as json_mod

            raw = await self.llm.chat(
                system=(
                    "You are a financial thread filter. "
                    "Return ONLY raw, valid JSON. Do not include markdown "
                    "formatting, code blocks like ```json, or conversational text."
                ),
                user=prompt,
                response_format="json",
                temperature=settings.LLM_DISCOVERY_TEMPERATURE,
            )
            cleaned = LLMService.clean_json_response(raw)
            indexes = json_mod.loads(cleaned)
            if isinstance(indexes, list):
                selected = [
                    candidates[i]
                    for i in indexes
                    if isinstance(i, int) and 0 <= i < len(candidates)
                ]
                logger.info(
                    "[Reddit]   -> LLM selected %d/%d threads",
                    len(selected),
                    len(candidates),
                )
                return selected
        except Exception as e:
            logger.warning("[Reddit] LLM filter failed: %s — using all candidates", e)

        # Fallback: return all candidates if LLM fails
        return candidates

    # ── Step 4: Deep scrape ─────────────────────────────────────────

    def get_thread_data(self, permalink: str) -> tuple[str, str, list[str], int]:
        """Fetch full thread content (title, body, top comments, comment count).

        Returns:
            (title, selftext, comments_list, total_comment_count)
        """
        url = f"https://www.reddit.com{permalink}.json"
        try:
            resp = requests.get(url, headers=self._get_headers(), timeout=10)
            if resp.status_code != 200:
                logger.warning("[Reddit] Thread fetch %d: %s", resp.status_code, url)
                return "", "", [], 0

            data = resp.json()
            title = ""
            body = ""
            num_comments = 0
            comments: list[str] = []

            # Extract post
            if isinstance(data, list) and len(data) > 0:
                post_listing = data[0]
                if "data" in post_listing and "children" in post_listing["data"]:
                    children = post_listing["data"]["children"]
                    if children and children[0]["kind"] == "t3":
                        post_data = children[0]["data"]
                        title = post_data.get("title", "")
                        body = post_data.get("selftext", "")
                        num_comments = post_data.get("num_comments", 0)

            # Extract comments
            if isinstance(data, list) and len(data) > 1:
                comment_listing = data[1]
                if "data" in comment_listing and "children" in comment_listing["data"]:
                    for child in comment_listing["data"]["children"]:
                        if child.get("kind") == "t1":
                            c_body = child["data"].get("body", "")
                            if c_body and c_body not in ("[deleted]", "[removed]"):
                                comments.append(c_body)
                                if len(comments) >= self.MAX_COMMENTS_PER_THREAD:
                                    break

            logger.info(
                "[Reddit]   Thread: '%s' — %d comments fetched (%d total)",
                title[:50],
                len(comments),
                num_comments,
            )
            return title, body, comments, num_comments

        except Exception as e:
            logger.warning("[Reddit] Thread scrape error: %s", e)
            return "", "", [], 0

    # ── Step 5: Extract and score tickers ───────────────────────────

    def extract_tickers(self, text: str) -> list[str]:
        """Regex extraction of uppercase 2-5 char words, filtered by exclusion list.

        Ambiguous tickers (words that are also common English like AI, IT)
        are filtered out here since regex can't determine context.
        The async disambiguator in collect() handles context-aware checks.
        """
        if not text:
            return []
        from app.services.ContextDisambiguator import AMBIGUOUS_TICKERS

        raw = re.findall(r"(?:\$|\b)([A-Z]{2,5})\b", text)
        return list({
            t for t in raw
            if t.isalpha()
            and t not in TickerValidator.EXCLUSION_LIST
            and t not in AMBIGUOUS_TICKERS
        })

    # ── Step 6: Stock-specific search ──────────────────────────────

    def search_for_ticker(
        self,
        ticker: str,
        *,
        time_filter: str = "week",
    ) -> list[dict[str, Any]]:
        """Search Reddit for threads mentioning a specific ticker.

        Uses Reddit's search JSON API to find recent discussions about
        a stock across financial subreddits.

        Args:
            ticker: Stock symbol (e.g. "AAPL")
            time_filter: Reddit time filter — "day", "week", "month"

        Returns:
            List of post dicts matching the subreddit fetch format.
        """
        results: list[dict[str, Any]] = []
        queries = [f"${ticker}", ticker]

        logger.info(
            "[Reddit] Step 6: Searching for $%s across %d subreddits...",
            ticker,
            len(self.SUBREDDITS_SEARCH),
        )

        for sub in self.SUBREDDITS_SEARCH:
            for query in queries:
                url = (
                    f"https://www.reddit.com/r/{sub}/search.json"
                    f"?q={query}&restrict_sr=1&sort=new&t={time_filter}"
                    f"&limit={self.MAX_SEARCH_RESULTS}"
                )

                try:
                    resp = requests.get(
                        url, headers=self._get_headers(), timeout=10,
                    )
                    if resp.status_code == 429:
                        time.sleep(3)
                        continue

                    if resp.status_code != 200:
                        continue

                    data = resp.json()
                    if "data" not in data or "children" not in data["data"]:
                        continue

                    for child in data["data"]["children"]:
                        pd = child["data"]
                        post = {
                            "title": pd.get("title", ""),
                            "subreddit": pd.get("subreddit", ""),
                            "permalink": pd.get("permalink", ""),
                            "score": pd.get("score", 0),
                            "selftext": pd.get("selftext", ""),
                            "stickied": pd.get("stickied", False),
                            "id": pd.get("id", ""),
                            "search_ticker": ticker,
                        }
                        results.append(post)

                    time.sleep(1)  # Rate limit between search requests

                except Exception as e:
                    logger.warning(
                        "[Reddit] Search error for $%s in r/%s: %s",
                        ticker, sub, e,
                    )
                    continue

                # Only use first query that yields results per subreddit
                if results:
                    break

        # Deduplicate by permalink
        seen: dict[str, dict] = {}
        for r in results:
            if r["permalink"] not in seen:
                seen[r["permalink"]] = r
        results = list(seen.values())

        logger.info(
            "[Reddit] Found %d unique threads for $%s via search",
            len(results),
            ticker,
        )
        return results

    # ── Full pipeline ───────────────────────────────────────────────

    async def collect(self) -> list[ScoredTicker]:
        """Run the full Reddit collection pipeline.

        The sync HTTP work (requests.get + time.sleep) is offloaded to a
        thread via asyncio.to_thread so it doesn't block the event loop
        and starve other collectors running in parallel.
        """
        # ── 4-hour cooldown guard ──
        # Only count rows with actual reddit context (subreddit names),
        # not RSS-sourced junk that leaked in as source='reddit'.
        from app.database import get_db

        db = get_db()
        last_run = db.execute(
            "SELECT MAX(discovered_at) FROM discovered_tickers "
            "WHERE source = 'reddit' "
            "AND source_detail NOT LIKE '%news articles%'"
        ).fetchone()
        if last_run and last_run[0]:
            hours_since = (datetime.now() - last_run[0]).total_seconds() / 3600
            if hours_since < 4:
                logger.info(
                    "[Reddit] Already scraped %.1fh ago, skipping (4h cooldown)",
                    hours_since,
                )
                return []

        start = time.time()
        logger.info("=" * 60)
        logger.info("[Reddit] Starting collection run")
        logger.info("=" * 60)

        # Step 1 + 2: Get threads (blocking I/O → run in thread)
        priority, trending = await asyncio.to_thread(
            self._sync_fetch_threads,
        )

        # Step 3: LLM filter on trending candidates (async LLM call)
        filtered = await self.filter_with_llm(trending)

        # Combine and deduplicate by permalink
        all_threads = priority + filtered
        unique = {t["permalink"]: t for t in all_threads if "permalink" in t}
        threads = list(unique.values())[: self.MAX_THREADS_TO_SCRAPE]

        # ── Already-seen filter: skip threads we've already scraped ──
        # Check which Reddit URLs are already in discovered_tickers.
        # If a thread was scraped before, its data is in the DB — skip it.
        try:
            seen_urls = set()
            rows = db.execute(
                "SELECT DISTINCT source_url FROM discovered_tickers "
                "WHERE source = 'reddit' AND source_url IS NOT NULL"
            ).fetchall()
            seen_urls = {r[0] for r in rows if r[0]}

            # Also check reddit_threads table
            seen_ids = set()
            try:
                id_rows = db.execute(
                    "SELECT thread_id FROM reddit_threads"
                ).fetchall()
                seen_ids = {r[0] for r in id_rows if r[0]}
            except Exception:
                pass  # Table may not exist yet

            before_count = len(threads)
            threads = [
                t for t in threads
                if (f"https://www.reddit.com{t['permalink']}" not in seen_urls
                    and t.get("id", "") not in seen_ids)
            ]
            skipped = before_count - len(threads)
            if skipped > 0:
                logger.info(
                    "[Reddit] Skipped %d already-scraped threads (%d new)",
                    skipped,
                    len(threads),
                )
        except Exception as exc:
            logger.warning("[Reddit] Already-seen check failed: %s — scraping all", exc)

        if not threads:
            logger.info("[Reddit] All threads already scraped — nothing new to process")
            return []

        logger.info("[Reddit] Step 4: Scraping %d NEW threads...", len(threads))

        # Step 4 + 5: Scrape and score (blocking I/O → run in thread)
        ticker_counts, ticker_contexts = await asyncio.to_thread(
            self._sync_scrape_threads,
            threads,
        )

        # ── Disambiguate ambiguous tickers ──────────────────────
        # Use the collected context snippets as source text for LLM check
        try:
            from app.services.ContextDisambiguator import (
                AMBIGUOUS_TICKERS,
                ContextDisambiguator,
            )

            ambiguous_found = [
                t for t in ticker_counts if t in AMBIGUOUS_TICKERS
            ]
            if ambiguous_found:
                logger.info(
                    "[Reddit] Disambiguating %d ambiguous tickers: %s",
                    len(ambiguous_found),
                    ambiguous_found,
                )
                # Build context text from thread snippets for each ambiguous ticker
                context_parts = []
                for t in ambiguous_found:
                    snippets = ticker_contexts.get(t, [])
                    for snippet, _url in snippets[:3]:
                        context_parts.append(snippet)
                context_text = "\n".join(context_parts)

                disambiguator = ContextDisambiguator()
                confirmed = await disambiguator.disambiguate(
                    ambiguous_found, context_text,
                )

                # Remove rejected ambiguous tickers
                rejected = set(ambiguous_found) - set(confirmed)
                for t in rejected:
                    del ticker_counts[t]
                    ticker_contexts.pop(t, None)
                    logger.info("[Reddit] Removed false-positive ticker: %s", t)
        except Exception as e:
            logger.warning("[Reddit] Disambiguation failed: %s — continuing", e)

        # Validate tickers
        logger.info(
            "[Reddit] Step 5: Validating %d candidate tickers...",
            len(ticker_counts),
        )
        valid_tickers = self.validator.validate_batch(list(ticker_counts.keys()))

        # Build scored results
        now = datetime.now()
        results: list[ScoredTicker] = []
        for ticker in valid_tickers:
            ctx_pairs = ticker_contexts.get(ticker, [])
            # Deduplicate snippets while keeping their URLs
            seen_snippets: dict[str, str] = {}
            for snippet, url in ctx_pairs:
                if snippet not in seen_snippets:
                    seen_snippets[snippet] = url
            deduped = list(seen_snippets.items())[:3]  # (snippet, url) pairs

            results.append(
                ScoredTicker(
                    ticker=ticker,
                    discovery_score=float(ticker_counts.get(ticker, 0)),
                    source="reddit",
                    source_detail=", ".join(set(t.get("subreddit", "") for t in threads)),
                    sentiment_hint="neutral",  # Could enhance with LLM later
                    context_snippets=[s for s, _u in deduped],
                    source_urls=[u for _s, u in deduped],
                    first_seen=now,
                    last_seen=now,
                )
            )

        # Sort by score descending
        results.sort(key=lambda x: x.discovery_score, reverse=True)

        elapsed = time.time() - start
        logger.info(
            "[Reddit] Collection complete: %d valid tickers in %.1fs",
            len(results),
            elapsed,
        )
        for r in results[:10]:
            logger.info(
                "[Reddit]   $%s: %.0f pts — %s",
                r.ticker,
                r.discovery_score,
                r.context_snippets[0] if r.context_snippets else "no context",
            )

        return results

    # ── Targeted collection for a specific stock ────────────────────

    async def collect_for_ticker(self, ticker: str) -> list[dict[str, Any]]:
        """Search Reddit specifically for a stock and persist thread data.

        Used by the deep analysis pipeline to gather Reddit sentiment
        for stocks already on the watchlist.

        Returns:
            List of thread dicts stored in reddit_threads.
        """
        logger.info("[Reddit] Targeted collection for $%s", ticker)

        # Search for the ticker (blocking I/O → thread)
        search_results = await asyncio.to_thread(
            self.search_for_ticker, ticker,
        )

        if not search_results:
            logger.info("[Reddit] No search results for $%s", ticker)
            return []

        # Deep scrape each result and persist
        stored_threads = await asyncio.to_thread(
            self._sync_scrape_and_persist,
            search_results,
            ticker,
        )

        logger.info(
            "[Reddit] Stored %d threads for $%s",
            len(stored_threads),
            ticker,
        )
        return stored_threads

    # ── Sync helpers (run inside asyncio.to_thread) ──────────────

    def _sync_fetch_threads(
        self,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Fetch priority + trending threads (blocking HTTP)."""
        priority = self.get_priority_threads()
        trending = self.get_trending_candidates()
        return priority, trending

    def _sync_scrape_threads(
        self,
        threads: list[dict[str, Any]],
    ) -> tuple[dict[str, int], dict[str, list[tuple[str, str]]]]:
        """Deep-scrape threads and extract tickers (blocking HTTP).

        Also persists full thread data to the reddit_threads table.
        """
        ticker_counts: dict[str, int] = {}
        ticker_contexts: dict[str, list[tuple[str, str]]] = {}

        for thread in threads:
            permalink = thread["permalink"]
            thread_url = f"https://www.reddit.com{permalink}"
            thread_id = thread.get("id", "")
            subreddit = thread.get("subreddit", "")
            search_ticker = thread.get("search_ticker", "")

            title, body, comments, num_comments = self.get_thread_data(permalink)
            time.sleep(1)  # Respect Reddit rate limits

            # ── Persist full thread data to reddit_threads ──
            if title and thread_id:
                self._store_thread(
                    thread_id=thread_id,
                    subreddit=subreddit,
                    title=title,
                    selftext=body,
                    permalink=permalink,
                    score=thread.get("score", 0),
                    num_comments=num_comments,
                    comments=comments,
                    search_ticker=search_ticker,
                )

            # ── Build rich context snippets ──
            # Combine title + body excerpt + comment excerpts (up to 500 chars)
            rich_context = self._build_rich_context(title, body, comments)

            # Weighted scoring
            for t in self.extract_tickers(title):
                ticker_counts[t] = ticker_counts.get(t, 0) + 3
                ticker_contexts.setdefault(t, []).append((rich_context, thread_url))

            for t in self.extract_tickers(body):
                ticker_counts[t] = ticker_counts.get(t, 0) + 2
                if t not in ticker_contexts or not any(
                    ctx == rich_context for ctx, _ in ticker_contexts[t]
                ):
                    ticker_contexts.setdefault(t, []).append((rich_context, thread_url))

            for comment in comments:
                for t in self.extract_tickers(comment):
                    ticker_counts[t] = ticker_counts.get(t, 0) + 1
                    if t not in ticker_contexts or not any(
                        ctx == rich_context for ctx, _ in ticker_contexts[t]
                    ):
                        ticker_contexts.setdefault(t, []).append(
                            (rich_context, thread_url)
                        )

            # Also find tickers for thread-level storage
            all_text = f"{title} {body} " + " ".join(comments)
            thread_tickers = self.extract_tickers(all_text)
            if thread_tickers and thread_id:
                self._update_thread_tickers(thread_id, thread_tickers)

        return ticker_counts, ticker_contexts

    def _sync_scrape_and_persist(
        self,
        threads: list[dict[str, Any]],
        search_ticker: str,
    ) -> list[dict[str, Any]]:
        """Scrape and persist threads found via stock-specific search."""
        stored: list[dict[str, Any]] = []

        for thread in threads:
            permalink = thread["permalink"]
            thread_id = thread.get("id", "")
            subreddit = thread.get("subreddit", "")

            # Skip if already stored
            try:
                from app.database import get_db
                db = get_db()
                existing = db.execute(
                    "SELECT 1 FROM reddit_threads WHERE thread_id = ?",
                    [thread_id],
                ).fetchone()
                if existing:
                    continue
            except Exception:
                pass

            title, body, comments, num_comments = self.get_thread_data(permalink)
            time.sleep(1)  # Rate limit

            if not title:
                continue

            # Find tickers in the thread
            all_text = f"{title} {body} " + " ".join(comments)
            thread_tickers = self.extract_tickers(all_text)
            # Ensure the search ticker is included
            if search_ticker not in thread_tickers:
                thread_tickers.append(search_ticker)

            self._store_thread(
                thread_id=thread_id,
                subreddit=subreddit,
                title=title,
                selftext=body,
                permalink=permalink,
                score=thread.get("score", 0),
                num_comments=num_comments,
                comments=comments,
                tickers=thread_tickers,
                search_ticker=search_ticker,
            )

            stored.append({
                "thread_id": thread_id,
                "subreddit": subreddit,
                "title": title,
                "permalink": permalink,
                "score": thread.get("score", 0),
                "tickers": thread_tickers,
                "num_comments": num_comments,
                "comment_count_fetched": len(comments),
            })

        return stored

    # ── Thread persistence helpers ──────────────────────────────────

    @staticmethod
    def _store_thread(
        *,
        thread_id: str,
        subreddit: str,
        title: str,
        selftext: str,
        permalink: str,
        score: int,
        num_comments: int,
        comments: list[str],
        tickers: list[str] | None = None,
        search_ticker: str = "",
    ) -> None:
        """Persist a thread to the reddit_threads table."""
        try:
            from app.database import get_db
            db = get_db()

            comments_json = json.dumps(comments[:25])  # Keep top 25 comments
            tickers_str = ",".join(tickers) if tickers else ""

            db.execute(
                """
                INSERT INTO reddit_threads
                    (thread_id, subreddit, title, selftext, permalink,
                     score, num_comments, comments_json, tickers_found,
                     search_ticker, collected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (thread_id) DO UPDATE SET
                    score = EXCLUDED.score,
                    num_comments = EXCLUDED.num_comments,
                    comments_json = EXCLUDED.comments_json,
                    tickers_found = EXCLUDED.tickers_found
                """,
                [
                    thread_id, subreddit, title, selftext[:10000], permalink,
                    score, num_comments, comments_json, tickers_str,
                    search_ticker, datetime.now(),
                ],
            )
            db.commit()
        except Exception as e:
            logger.warning("[Reddit] Failed to store thread %s: %s", thread_id, e)

    @staticmethod
    def _update_thread_tickers(thread_id: str, tickers: list[str]) -> None:
        """Update the tickers_found field for a thread."""
        try:
            from app.database import get_db
            db = get_db()
            tickers_str = ",".join(tickers)
            db.execute(
                "UPDATE reddit_threads SET tickers_found = ? WHERE thread_id = ?",
                [tickers_str, thread_id],
            )
            db.commit()
        except Exception:
            pass  # Best-effort

    @staticmethod
    def _build_rich_context(
        title: str,
        body: str,
        comments: list[str],
        max_len: int = 500,
    ) -> str:
        """Build a rich context string from thread data.

        Combines title + body excerpt + top comment excerpts into
        a single string, capped at max_len characters.
        """
        parts: list[str] = []

        # Title always included
        if title:
            parts.append(f"Thread: {title}")

        # Body excerpt
        if body:
            body_clean = body.strip()[:250]
            if body_clean:
                parts.append(f"Post: {body_clean}")

        # Top comment excerpts
        if comments:
            comment_parts = []
            for c in comments[:3]:
                excerpt = c.strip()[:120]
                if excerpt:
                    comment_parts.append(excerpt)
            if comment_parts:
                parts.append(f"Comments: {'; '.join(comment_parts)}")

        result = " | ".join(parts)
        return result[:max_len] if len(result) > max_len else result

    # ── Helpers ──────────────────────────────────────────────────────

    def _fetch_subreddit(self, subreddit: str, listing: str, limit: int) -> list[dict[str, Any]]:
        """Fetch posts from a subreddit using the public JSON API.

        Retries with exponential backoff on 429 (rate limit) responses.
        """
        url = f"https://www.reddit.com/r/{subreddit}/{listing}.json?limit={limit}"
        logger.info("[Reddit] Fetching %s from r/%s...", listing, subreddit)

        for attempt in range(self.MAX_RETRIES):
            try:
                resp = requests.get(
                    url,
                    headers=self._get_headers(),
                    timeout=15,
                )
                if resp.status_code == 429:
                    wait = 2 ** (attempt + 1)  # 2s, 4s, 8s
                    logger.warning(
                        "[Reddit] Rate limited on r/%s, waiting %ds (attempt %d/%d)",
                        subreddit,
                        wait,
                        attempt + 1,
                        self.MAX_RETRIES,
                    )
                    time.sleep(wait)
                    continue

                if resp.status_code != 200:
                    logger.warning(
                        "[Reddit] r/%s returned %d — %s",
                        subreddit,
                        resp.status_code,
                        resp.text[:200],
                    )
                    return []

                data = resp.json()
                posts: list[dict[str, Any]] = []
                if "data" in data and "children" in data["data"]:
                    for child in data["data"]["children"]:
                        pd = child["data"]
                        posts.append(
                            {
                                "title": pd.get("title", ""),
                                "subreddit": pd.get("subreddit", ""),
                                "permalink": pd.get("permalink", ""),
                                "score": pd.get("score", 0),
                                "selftext": pd.get("selftext", ""),
                                "stickied": pd.get("stickied", False),
                                "id": pd.get("id", ""),
                            }
                        )
                logger.info(
                    "[Reddit] r/%s/%s: %d posts fetched",
                    subreddit,
                    listing,
                    len(posts),
                )
                return posts

            except Exception as e:
                logger.warning(
                    "[Reddit] Fetch error for r/%s (attempt %d): %s",
                    subreddit,
                    attempt + 1,
                    e,
                )
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(2**attempt)

        logger.error("[Reddit] All %d attempts failed for r/%s", self.MAX_RETRIES, subreddit)
        return []

    # ── Static helpers for external callers ──────────────────────────

    @staticmethod
    def get_threads_for_ticker(ticker: str, limit: int = 10) -> list[dict[str, Any]]:
        """Retrieve stored Reddit threads mentioning a ticker.

        Used by the data distiller and API endpoints to get rich
        thread data for a specific stock.
        """
        try:
            from app.database import get_db
            db = get_db()

            rows = db.execute(
                """
                SELECT thread_id, subreddit, title, selftext,
                       permalink, score, num_comments, comments_json,
                       tickers_found, search_ticker, collected_at
                FROM reddit_threads
                WHERE tickers_found LIKE ?
                   OR search_ticker = ?
                ORDER BY collected_at DESC
                LIMIT ?
                """,
                [f"%{ticker}%", ticker, limit],
            ).fetchall()

            threads = []
            for r in rows:
                comments = []
                try:
                    comments = json.loads(r[7]) if r[7] else []
                except (json.JSONDecodeError, TypeError):
                    pass

                threads.append({
                    "thread_id": r[0],
                    "subreddit": r[1],
                    "title": r[2],
                    "selftext": r[3],
                    "permalink": r[4],
                    "score": r[5],
                    "num_comments": r[6],
                    "comments": comments,
                    "tickers_found": r[8],
                    "search_ticker": r[9],
                    "collected_at": str(r[10]) if r[10] else None,
                })

            return threads

        except Exception as e:
            logger.warning("[Reddit] get_threads_for_ticker error: %s", e)
            return []
