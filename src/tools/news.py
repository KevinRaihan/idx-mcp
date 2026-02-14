"""get_stock_news tool — Recent news from Indonesian financial media."""

import logging

from ..scrapers.news_sites import scrape_news
from ..utils.cache import TTLCache, cache
from ..utils.ticker import validate_ticker
from ..utils.time_utils import now_wib

logger = logging.getLogger("idx-mcp.tools.news")


async def get_stock_news(ticker: str, limit: int = 10) -> dict:
    """Get recent news for a stock from Indonesian financial media.

    Args:
        ticker: IDX ticker symbol
        limit: Max articles to return (default 10, max 20)

    Returns:
        Dict with news articles or error response.
    """
    try:
        normalized = validate_ticker(ticker)
    except ValueError as e:
        return {
            "error": True,
            "error_type": "invalid_ticker",
            "message": str(e),
            "partial_data": None,
            "suggestion": "Check the ticker symbol.",
        }

    # Clamp limit
    if not isinstance(limit, int) or limit < 1:
        limit = 10
    limit = min(limit, 20)

    cached = cache.get("get_stock_news", normalized, {"limit": limit})
    if cached is not None:
        return cached

    try:
        data = await scrape_news(normalized, limit=limit)

        result = {
            "ticker": normalized,
            "articles": data.get("articles", []),
            "article_count": data.get("article_count", 0),
            "sources_searched": data.get("sources_searched", []),
            "search_date": now_wib().strftime("%Y-%m-%d"),
        }

        if data.get("sources_failed"):
            result["sources_failed"] = data["sources_failed"]
            result["note"] = f"Some sources were unreachable: {', '.join(data['sources_failed'])}"

        cache.set("get_stock_news", normalized, result, TTLCache.TTL_NEWS, {"limit": limit})
        return result

    except Exception as e:
        logger.exception(f"Error fetching news for {normalized}")
        return {
            "error": True,
            "error_type": "scrape_failed",
            "message": f"Failed to fetch news for {normalized}: {str(e)}",
            "partial_data": {"ticker": normalized},
            "suggestion": "Try again later. News sources may be temporarily unavailable.",
        }
