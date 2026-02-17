"""Reddit Collector — scrapes financial subreddits for trending ticker mentions.

Uses Reddit's public JSON API (no auth needed). Adapted from the
RedditPurgeScraper reference implementation in example_repos/.

Pipeline:
    1. Get priority threads (stickied "Daily Discussion" etc.)
    2. Get rising/trending candidates
    3. LLM filters the most promising threads
    4. Deep scrape: title + body + top comments
    5. Extract tickers via regex, validate, and score
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any

import requests
from fake_useragent import UserAgent

from app.collectors.ticker_validator import TickerValidator
from app.models.discovery import ScoredTicker
from app.services.llm_service import LLMService
from app.utils.logger import logger


class RedditCollector:
    """Scrapes financial subreddits for trending ticker mentions."""

    # Configurable subreddit lists
    SUBREDDITS_PRIORITY = ["wallstreetbets", "stocks"]
    SUBREDDITS_TRENDING = ["wallstreetbets", "pennystocks"]

    # Limit to 1 sub each for debug mode (user requested fast iteration)
    MAX_POSTS_PER_SUB = 3
    MAX_COMMENTS_PER_THREAD = 10
    MAX_THREADS_TO_SCRAPE = 3

    def __init__(self) -> None:
        self.validator = TickerValidator()
        self.llm = LLMService()

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
                        title[:60], sub,
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

    async def filter_with_llm(
        self, candidates: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Ask LLM to pick threads likely discussing stock catalysts."""
        if not candidates:
            return []

        logger.info(
            "[Reddit] Step 3: Filtering %d candidates with LLM...",
            len(candidates),
        )

        titles_text = "\n".join(
            f"{i}. {p['title']} (r/{p['subreddit']})"
            for i, p in enumerate(candidates)
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
                system="You are a financial thread filter. Return ONLY valid JSON.",
                user=prompt,
                response_format="json",
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
                    len(selected), len(candidates),
                )
                return selected
        except Exception as e:
            logger.warning("[Reddit] LLM filter failed: %s — using all candidates", e)

        # Fallback: return all candidates if LLM fails
        return candidates

    # ── Step 4: Deep scrape ─────────────────────────────────────────

    def get_thread_data(
        self, permalink: str
    ) -> tuple[str, str, list[str]]:
        """Fetch full thread content (title, body, top comments)."""
        url = f"https://www.reddit.com{permalink}.json"
        try:
            resp = requests.get(url, headers=self._get_headers(), timeout=10)
            if resp.status_code != 200:
                logger.warning("[Reddit] Thread fetch %d: %s", resp.status_code, url)
                return "", "", []

            data = resp.json()
            title = ""
            body = ""
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
                "[Reddit]   Thread: '%s' — %d comments",
                title[:50], len(comments),
            )
            return title, body, comments

        except Exception as e:
            logger.warning("[Reddit] Thread scrape error: %s", e)
            return "", "", []

    # ── Step 5: Extract and score tickers ───────────────────────────

    def extract_tickers(self, text: str) -> list[str]:
        """Regex extraction of uppercase 2-5 char words, filtered by exclusion list."""
        if not text:
            return []
        raw = re.findall(r"(?:\$|\b)([A-Z]{2,5})\b", text)
        return list({
            t for t in raw
            if t.isalpha() and t not in TickerValidator.EXCLUSION_LIST
        })

    # ── Full pipeline ───────────────────────────────────────────────

    async def collect(self) -> list[ScoredTicker]:
        """Run the full Reddit collection pipeline."""
        start = time.time()
        logger.info("=" * 60)
        logger.info("[Reddit] Starting collection run")
        logger.info("=" * 60)

        # Step 1 + 2: Get threads
        priority = self.get_priority_threads()
        trending = self.get_trending_candidates()

        # Step 3: LLM filter on trending candidates
        filtered = await self.filter_with_llm(trending)

        # Combine and deduplicate by permalink
        all_threads = priority + filtered
        unique = {t["permalink"]: t for t in all_threads if "permalink" in t}
        threads = list(unique.values())[: self.MAX_THREADS_TO_SCRAPE]

        logger.info("[Reddit] Step 4: Scraping %d unique threads...", len(threads))

        # Step 4 + 5: Scrape and score
        ticker_counts: dict[str, int] = {}
        ticker_contexts: dict[str, list[str]] = {}

        for thread in threads:
            title, body, comments = self.get_thread_data(thread["permalink"])
            time.sleep(1)  # Respect Reddit rate limits

            # Weighted scoring
            for t in self.extract_tickers(title):
                ticker_counts[t] = ticker_counts.get(t, 0) + 3
                ticker_contexts.setdefault(t, []).append(f"[title] {title[:80]}")

            for t in self.extract_tickers(body):
                ticker_counts[t] = ticker_counts.get(t, 0) + 2
                ticker_contexts.setdefault(t, []).append(f"[body] {body[:80]}")

            for comment in comments:
                for t in self.extract_tickers(comment):
                    ticker_counts[t] = ticker_counts.get(t, 0) + 1
                    ticker_contexts.setdefault(t, []).append(
                        f"[comment] {comment[:60]}"
                    )

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
            results.append(
                ScoredTicker(
                    ticker=ticker,
                    discovery_score=float(ticker_counts.get(ticker, 0)),
                    source="reddit",
                    source_detail=", ".join(
                        set(
                            t.get("subreddit", "")
                            for t in threads
                        )
                    ),
                    sentiment_hint="neutral",  # Could enhance with LLM later
                    context_snippets=ticker_contexts.get(ticker, [])[:3],
                    first_seen=now,
                    last_seen=now,
                )
            )

        # Sort by score descending
        results.sort(key=lambda x: x.discovery_score, reverse=True)

        elapsed = time.time() - start
        logger.info(
            "[Reddit] Collection complete: %d valid tickers in %.1fs",
            len(results), elapsed,
        )
        for r in results[:10]:
            logger.info(
                "[Reddit]   $%s: %.0f pts — %s",
                r.ticker, r.discovery_score,
                r.context_snippets[0] if r.context_snippets else "no context",
            )

        return results

    # ── Helpers ──────────────────────────────────────────────────────

    def _fetch_subreddit(
        self, subreddit: str, listing: str, limit: int
    ) -> list[dict[str, Any]]:
        """Fetch posts from a subreddit using the public JSON API."""
        url = f"https://www.reddit.com/r/{subreddit}/{listing}.json?limit={limit}"
        logger.debug("[Reddit] Fetching %s from r/%s...", listing, subreddit)

        try:
            resp = requests.get(url, headers=self._get_headers(), timeout=10)
            if resp.status_code == 429:
                logger.warning("[Reddit] Rate limited, waiting 5s...")
                time.sleep(5)
                resp = requests.get(url, headers=self._get_headers(), timeout=10)

            if resp.status_code != 200:
                logger.warning(
                    "[Reddit] r/%s returned %d", subreddit, resp.status_code
                )
                return []

            data = resp.json()
            posts: list[dict[str, Any]] = []
            if "data" in data and "children" in data["data"]:
                for child in data["data"]["children"]:
                    pd = child["data"]
                    posts.append({
                        "title": pd.get("title", ""),
                        "subreddit": pd.get("subreddit", ""),
                        "permalink": pd.get("permalink", ""),
                        "score": pd.get("score", 0),
                        "selftext": pd.get("selftext", ""),
                        "stickied": pd.get("stickied", False),
                        "id": pd.get("id", ""),
                    })
            return posts

        except Exception as e:
            logger.warning("[Reddit] Fetch error for r/%s: %s", subreddit, e)
            return []
