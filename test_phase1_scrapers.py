import asyncio
import sys
import logging

# Ensure app imports work
import os
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.services.yfinance_service import YFinanceCollector
from app.services.news_service import NewsCollector
from app.services.youtube_service import YouTubeCollector
from app.services.sec_13f_service import SEC13FCollector
from app.services.congress_service import CongressCollector
from app.services.rss_news_service import RSSNewsCollector
from app.services.reddit_service import RedditCollector
from app.config import settings
from pathlib import Path

# Override DB path to an isolated audit DB so we don't conflict with the running server's DuckDB lock
settings.DB_PATH = Path("/home/braindead/development/Lazy-Trading-Bot/data/trading_bot_audit_scratch.duckdb")

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("Phase1Audit")

async def test_scrapers(ticker: str):
    logger.info(f"=== Starting Phase 1 Scraper Audit for {ticker} ===")
    
    results = {}
    
    try:
        yf = YFinanceCollector()
        logger.info("[1/7] Testing YFinanceCollector...")
        price = await yf.collect_price_history(ticker)
        fund = await yf.collect_fundamentals(ticker)
        results["yfinance_price_rows"] = len(price) if price else 0
        results["yfinance_fund_valid"] = bool(fund)
        logger.info(f"YFinance: {results['yfinance_price_rows']} price rows, Fundamentals OK: {results['yfinance_fund_valid']}")
    except Exception as e:
        logger.error(f"YFinance failed: {e}")
        results["yfinance_error"] = str(e)

    try:
        news = NewsCollector()
        logger.info("[2/7] Testing NewsCollector...")
        await news.collect(ticker)
        news_data = await news.get_all_historical(ticker)
        results["news_articles"] = len(news_data) if news_data else 0
        logger.info(f"NewsCollector: {results['news_articles']} articles")
    except Exception as e:
        logger.error(f"NewsCollector failed: {e}")
        results["news_error"] = str(e)

    try:
        yt = YouTubeCollector()
        logger.info("[3/7] Testing YouTubeCollector...")
        await yt.collect(ticker)
        yt_data = await yt.get_all_historical(ticker)
        results["youtube_transcripts"] = len(yt_data) if yt_data else 0
        logger.info(f"YouTubeCollector: {results['youtube_transcripts']} transcripts")
    except Exception as e:
        logger.error(f"YouTubeCollector failed: {e}")
        results["youtube_error"] = str(e)

    try:
        sec = SEC13FCollector()
        logger.info("[4/7] Testing SEC13FCollector (Live Scrape)...")
        settings.SEC_13F_MAX_FILERS = 1  # only do 1 to save time
        live_tickers = await sec.collect_recent_holdings()
        sec_data = await sec.get_holdings_for_ticker(ticker)
        results["sec_live_tickers"] = len(live_tickers)
        results["sec_nvda_holdings"] = len(sec_data) if sec_data else 0
        logger.info(f"SEC13FCollector: {results['sec_live_tickers']} tickers scraped, {results['sec_nvda_holdings']} specific holdings")
    except Exception as e:
        logger.error(f"SEC13FCollector failed: {e}")
        results["sec_error"] = str(e)

    try:
        congress = CongressCollector()
        logger.info("[5/7] Testing CongressCollector (Live Scrape)...")
        live_trades = await congress.collect_recent_trades()
        congress_data = await congress.get_trades_for_ticker(ticker)
        results["congress_live_trades"] = len(live_trades)
        results["congress_nvda_trades"] = len(congress_data) if congress_data else 0
        logger.info(f"CongressCollector: {results['congress_live_trades']} top traded scraped, {results['congress_nvda_trades']} specific trades")
    except Exception as e:
        logger.error(f"CongressCollector failed: {e}")
        results["congress_error"] = str(e)

    try:
        rss = RSSNewsCollector()
        logger.info("[6/7] Testing RSSNewsCollector (Live Scrape)...")
        import app.services.rss_news_service as rss_module
        rss_module.RSS_FEEDS = rss_module.RSS_FEEDS[:2]  # limit feeds
        
        live_articles = await rss.scrape_all_feeds()
        rss_data = await rss.get_articles_for_ticker(ticker)
        results["rss_live_articles"] = len(live_articles)
        results["rss_nvda_articles"] = len(rss_data) if rss_data else 0
        logger.info(f"RSSNewsCollector: {results['rss_live_articles']} live articles scraped, {results['rss_nvda_articles']} specific articles")
    except Exception as e:
        logger.error(f"RSSNewsCollector failed: {e}")
        results["rss_error"] = str(e)
        
    try:
        reddit = RedditCollector()
        logger.info("[7/7] Testing RedditCollector...")
        # Discovery service runs it without ticker first, but let's test a simple Reddit scrape if there's a method
        if hasattr(reddit, 'scrape_ticker'):
            reddit_data = await reddit.scrape_ticker(ticker)
            results["reddit_posts"] = len(reddit_data) if reddit_data else 0
            logger.info(f"RedditCollector: {results['reddit_posts']} posts")
        else:
            logger.info("RedditCollector: Target scraping not directly supported, skipping.")
            results["reddit"] = "skipped"
    except Exception as e:
        logger.error(f"RedditCollector failed: {e}")
        results["reddit_error"] = str(e)

    logger.info("=== Audit Results ===")
    for k, v in results.items():
        logger.info(f"  {k}: {v}")

if __name__ == "__main__":
    asyncio.run(test_scrapers("NVDA"))
