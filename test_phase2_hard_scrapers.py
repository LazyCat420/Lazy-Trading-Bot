import asyncio
import logging
from unittest.mock import patch, AsyncMock, MagicMock
import httpx
import requests

from app.database import switch_db
from app.services.reddit_service import RedditCollector
from app.services.sec_13f_service import SEC13FCollector
from app.services.congress_service import CongressCollector
from app.services.youtube_service import YouTubeCollector

# Use test database
switch_db("test")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("hard_scrapers_test")

async def test_reddit_timeout():
    logger.info("--- Testing Reddit Service with TimeoutExceptions ---")
    svc = RedditCollector()
    
    with patch("requests.get") as mock_get:
        mock_get.side_effect = requests.exceptions.Timeout("Mocked Network Timeout!")
        
        try:
            result = await svc.collect()
            logger.info("Reddit handled timeout safely. Result length: %d", len(result))
        except Exception as e:
            logger.error("Reddit CRASHED on timeout: %s", type(e))

async def test_congress_json_corruption():
    logger.info("--- Testing Congress Service with JSONDecodeError ---")
    svc = CongressCollector()
    
    with patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status.return_value = None
        mock_response.json.side_effect = ValueError("Expecting value: line 1 column 1 (char 0)")
        mock_response.text = "<html><body>502 Bad Gateway</body></html>"
        mock_get.return_value = mock_response
        
        try:
            result = await svc.collect("NVDA")
            logger.info("Congress handled JSON corruption safely. Result: %s", result)
        except Exception as e:
            logger.error("Congress CRASHED on JSON corruption: %s", type(e))

async def test_youtube_context_overflow():
    logger.info("--- Testing YouTube Service with 50,000 token output ---")
    svc = YouTubeCollector()
    
    # Mock the transcript API to return 4 hours of text
    with patch("app.services.youtube_service.YouTubeTranscriptApi.get_transcript") as mock_transcript:
        mock_transcript.return_value = [{"text": "this is a long video transcript chunk " * 100, "start": i} for i in range(5000)]
        
        try:
            result = await svc.collect()
            # The distiller or the fetcher shouldn't blow up; it should just cap the size
            logger.info("YouTube handled 50,000 chunks safely. Output char length: %d", len(str(result)))
        except Exception as e:
            logger.error("YouTube CRASHED on massive context overflow: %s", type(e))

async def test_sec13f_403_forbidden():
    logger.info("--- Testing SEC 13F Service with Rate Limit / 403 Forbidden ---")
    svc = SEC13FCollector()
    
    with patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 403
        def raise_status():
            raise requests.exceptions.HTTPError("403 Forbidden", response=mock_response)
        mock_response.raise_for_status.side_effect = raise_status
        mock_get.return_value = mock_response
        
        try:
            result = await svc.collect("AAPL")
            logger.info("SEC13F handled 403 safely. Result: %s", result)
        except Exception as e:
            logger.error("SEC13F CRASHED on 403: %s", type(e))

async def main():
    await test_reddit_timeout()
    await test_congress_json_corruption()
    await test_youtube_context_overflow()
    await test_sec13f_403_forbidden()

if __name__ == "__main__":
    asyncio.run(main())
