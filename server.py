"""Server entry point."""

import logging
import uvicorn

from app.config import settings


# ── Quiet down high-frequency polling endpoints ──
SUPPRESSED_PATHS = {
    "/api/bot/loop-status",
    "/api/pipeline/events",
    "/api/llm/live",
    "/api/discovery/status",
    "/api/watchlist/summary",
    "/api/watchlist",
    "/api/portfolio",
    "/api/orders",
    "/api/triggers",
    "/api/leaderboard",
    "/api/dashboard/db-stats",
    "/api/portfolio/history",
    "/api/discovery/history",
    "/api/discovery/results",
}


class QuietAccessFilter(logging.Filter):
    """Suppress high-frequency polling GETs from Uvicorn access logs."""

    def filter(self, record):
        msg = record.getMessage()
        # Only suppress successful GET polls (200 OK)
        if "200 OK" not in msg:
            return True
        for path in SUPPRESSED_PATHS:
            if f"GET {path}" in msg:
                return False
        return True


if __name__ == "__main__":
    # Install filter on uvicorn's access logger
    logging.getLogger("uvicorn.access").addFilter(QuietAccessFilter())

    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=True,
        log_level="info",
    )
