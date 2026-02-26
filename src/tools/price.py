"""get_stock_price tool — Current/last price and basic trading data."""

import asyncio
import logging

import yfinance as yf

from ..utils.cache import TTLCache, cache
from ..utils.formatting import safe_round
from ..utils.ticker import to_yfinance_ticker, validate_ticker
from ..utils.time_utils import format_wib_iso, get_market_status

logger = logging.getLogger("idx-mcp.tools.price")


async def get_stock_price(ticker: str) -> dict:
    """Get current/last price and basic trading data for an IDX stock.

    Args:
        ticker: IDX ticker symbol (e.g., "BBCA")

    Returns:
        Dict with price data or error response.
    """
    try:
        normalized = validate_ticker(ticker)
    except ValueError as e:
        return {
            "error": True,
            "error_type": "invalid_ticker",
            "message": str(e),
            "partial_data": None,
            "suggestion": "Check the ticker symbol. IDX tickers are typically 4 uppercase letters (e.g., BBCA, TLKM).",
        }

    # Check cache
    cached = cache.get("get_stock_price", normalized)
    if cached is not None:
        return cached

    try:
        yf_ticker = to_yfinance_ticker(normalized)
        stock = yf.Ticker(yf_ticker)
        info = await asyncio.to_thread(lambda: stock.info)

        if not info or info.get("regularMarketPrice") is None:
            # Try fast_info as fallback — FastInfo uses attribute access, not .get()
            try:
                fi = await asyncio.to_thread(lambda: stock.fast_info)
                price = getattr(fi, "last_price", None) or getattr(fi, "lastPrice", None)
                if price is None:
                    return {
                        "error": True,
                        "error_type": "data_unavailable",
                        "message": f"No price data available for {normalized}. The ticker may be invalid or delisted.",
                        "partial_data": None,
                        "suggestion": f"Verify that {normalized} is a valid IDX ticker.",
                    }
            except Exception:
                return {
                    "error": True,
                    "error_type": "data_unavailable",
                    "message": f"No data available for {normalized}.",
                    "partial_data": None,
                    "suggestion": f"Verify that {normalized} is a valid IDX ticker.",
                }

        price = info.get("regularMarketPrice") or info.get("currentPrice")
        prev_close = info.get("regularMarketPreviousClose") or info.get("previousClose")
        open_price = info.get("regularMarketOpen") or info.get("open")
        high = info.get("regularMarketDayHigh") or info.get("dayHigh")
        low = info.get("regularMarketDayLow") or info.get("dayLow")
        volume = info.get("regularMarketVolume") or info.get("volume") or 0

        change = safe_round(price - prev_close, 2) if price and prev_close else None
        change_pct = safe_round((change / prev_close) * 100, 2) if change and prev_close else None

        volume_idr_billion = safe_round((volume * price) / 1_000_000_000, 2) if volume and price else None

        market_cap = info.get("marketCap")
        market_cap_trillion = safe_round(market_cap / 1_000_000_000_000, 2) if market_cap else None

        result = {
            "ticker": normalized,
            "name": info.get("longName") or info.get("shortName") or normalized,
            "price": price,
            "currency": "IDR",
            "change": change,
            "change_percent": change_pct,
            "open": open_price,
            "high": high,
            "low": low,
            "volume": volume,
            "volume_idr_billion": volume_idr_billion,
            "prev_close": prev_close,
            "week_52_high": info.get("fiftyTwoWeekHigh"),
            "week_52_low": info.get("fiftyTwoWeekLow"),
            "market_cap_trillion_idr": market_cap_trillion,
            "market_status": get_market_status(),
            "last_updated": format_wib_iso(),
            "source": "yfinance",
        }

        cache.set("get_stock_price", normalized, result, TTLCache.TTL_PRICE)
        return result

    except Exception as e:
        logger.exception(f"Error fetching price for {normalized}")
        return {
            "error": True,
            "error_type": "data_unavailable",
            "message": f"Failed to fetch price data for {normalized}: {str(e)}",
            "partial_data": None,
            "suggestion": "Try again later. The data source may be temporarily unavailable.",
        }
