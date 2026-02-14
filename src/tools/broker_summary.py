"""get_broker_summary tool — Top buying/selling brokers from Stockbit."""

import json
import logging
from pathlib import Path

from ..scrapers.stockbit import scrape_broker_summary
from ..utils.cache import TTLCache, cache
from ..utils.ticker import validate_ticker
from ..utils.time_utils import format_wib_iso, now_wib

logger = logging.getLogger("idx-mcp.tools.broker_summary")

# Broker type classification map
BROKER_TYPE_MAP = {
    "foreign": ["YP", "MS", "RX", "AK", "KI", "CG", "CS", "DB", "GS", "BW", "ML", "AI", "LG", "ZP"],
    "domestic_institutional": ["CC", "DX", "BK", "NI", "FS", "IF", "AZ", "OD", "TP", "KK", "DH", "YJ", "SQ", "XC", "MG"],
    "retail_heavy": ["PD", "KZ", "GR", "YU", "EP", "AG", "FZ", "AO", "KS", "DR", "CP", "ZR", "BG", "RI", "AT", "PS", "AP"],
}


async def get_broker_summary(ticker: str) -> dict:
    """Get top buying and selling brokers for a stock.

    Args:
        ticker: IDX ticker symbol

    Returns:
        Dict with broker summary data or error response.
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

    cached = cache.get("get_broker_summary", normalized)
    if cached is not None:
        return cached

    try:
        data = await scrape_broker_summary(normalized)

        result = {
            "ticker": normalized,
            "date": now_wib().strftime("%Y-%m-%d"),
            "top_buyers": data.get("top_buyers", []),
            "top_sellers": data.get("top_sellers", []),
            "summary": data.get("summary", {}),
            "broker_type_map": BROKER_TYPE_MAP,
            "source": "stockbit.com (scraped)",
            "scrape_note": "Data may be delayed or unavailable if Stockbit changes page structure",
        }

        cache.set("get_broker_summary", normalized, result, TTLCache.TTL_BROKER)
        return result

    except Exception as e:
        logger.exception(f"Error fetching broker summary for {normalized}")
        return {
            "error": True,
            "error_type": "scrape_failed",
            "message": f"Broker summary unavailable for {normalized}: {str(e)}. Stockbit page structure may have changed.",
            "partial_data": {
                "ticker": normalized,
                "broker_type_map": BROKER_TYPE_MAP,
            },
            "suggestion": "Try again later. Stockbit may be temporarily unavailable or may have changed its page structure.",
        }
