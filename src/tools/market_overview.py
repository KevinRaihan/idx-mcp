"""get_market_overview tool — IHSG level, sectors, macro snapshot."""

import asyncio
import logging

import yfinance as yf

from ..scrapers.idx import scrape_sector_indices
from ..utils.cache import TTLCache, cache
from ..utils.formatting import safe_round
from ..utils.time_utils import format_wib_iso, get_market_status

logger = logging.getLogger("idx-mcp.tools.market_overview")

# Commodity/macro yfinance tickers
MACRO_TICKERS = {
    "usd_idr": "USDIDR=X",
    "gold_usd": "GC=F",
    "nickel_lme_usd": "NI=F",
    "coal_newcastle_usd": "MTFc1",  # Newcastle coal futures
    "cpo_usd": "KO=F",  # Palm oil proxy (may not be exact)
}


async def get_market_overview(include_macro: bool = True) -> dict:
    """Get IDX market overview: IHSG level, sectoral indices, macro snapshot.

    Args:
        include_macro: Include macro data (BI rate, USD/IDR, etc.). Default True.

    Returns:
        Dict with market overview data or error response.
    """
    cached = cache.get("get_market_overview", "MARKET", {"include_macro": include_macro})
    if cached is not None:
        return cached

    try:
        # IHSG data
        ihsg = yf.Ticker("^JKSE")
        ihsg_info = await asyncio.to_thread(lambda: ihsg.info) or {}

        ihsg_price = ihsg_info.get("regularMarketPrice") or ihsg_info.get("previousClose")
        ihsg_prev = ihsg_info.get("regularMarketPreviousClose") or ihsg_info.get("previousClose")
        ihsg_change = safe_round(ihsg_price - ihsg_prev, 2) if ihsg_price and ihsg_prev else None
        ihsg_change_pct = safe_round((ihsg_change / ihsg_prev) * 100, 2) if ihsg_change and ihsg_prev else None
        ihsg_volume = ihsg_info.get("regularMarketVolume") or 0

        # Volume in trillion IDR (rough estimate)
        volume_trillion = safe_round(ihsg_volume / 1_000_000_000_000, 2) if ihsg_volume else None

        result = {
            "ihsg": {
                "value": safe_round(ihsg_price, 2),
                "change": ihsg_change,
                "change_percent": ihsg_change_pct,
                "volume_idr_trillion": volume_trillion,
            },
            "market_status": get_market_status(),
            "timestamp": format_wib_iso(),
        }

        # Sector performance (try scraping, fallback to empty)
        try:
            sectors = await scrape_sector_indices()
            result["sector_performance"] = sectors if sectors else []
        except Exception:
            result["sector_performance"] = []

        # Macro data
        if include_macro:
            macro = await _fetch_macro_data()
            result["macro"] = macro

        result["source"] = "yfinance + web scraping"

        cache.set("get_market_overview", "MARKET", result, TTLCache.TTL_MARKET, {"include_macro": include_macro})
        return result

    except Exception as e:
        logger.exception("Error fetching market overview")
        return {
            "error": True,
            "error_type": "data_unavailable",
            "message": f"Failed to fetch market overview: {str(e)}",
            "partial_data": None,
            "suggestion": "Try again later.",
        }


async def _fetch_macro_data() -> dict:
    """Fetch macro data from yfinance (USD/IDR, commodities)."""
    macro = {
        "bi_rate_pct": None,  # Must be manually updated or scraped from BI
        "usd_idr": None,
        "inflation_yoy_pct": None,  # Would need separate scraping
        "coal_newcastle_usd": None,
        "cpo_usd": None,
        "nickel_lme_usd": None,
        "gold_usd": None,
    }

    for key, yf_symbol in MACRO_TICKERS.items():
        try:
            t = yf.Ticker(yf_symbol)
            # Use default-arg binding (t=t) to capture the loop variable correctly
            info = await asyncio.to_thread(lambda t=t: t.info) or {}
            price = info.get("regularMarketPrice") or info.get("previousClose")
            if price:
                macro[key] = safe_round(price, 2)
        except Exception as e:
            logger.debug(f"Failed to fetch macro ticker {yf_symbol}: {e}")
            continue

    return macro
