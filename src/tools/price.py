"""get_stock_price tool — Current/last price and basic trading data."""

import asyncio
import logging
import math

import yfinance as yf

from ..utils.cache import TTLCache, cache
from ..utils.formatting import safe_round
from ..utils.ticker import to_yfinance_ticker, validate_ticker
from ..utils.time_utils import format_wib_iso, get_market_status

logger = logging.getLogger("idx-mcp.tools.price")

_TIMEOUT = 20.0  # seconds — yfinance info call should never take longer


async def get_stock_price(ticker: str) -> dict:
    """Get current/last price and basic trading data for an IDX stock."""
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

    cached = cache.get("get_stock_price", normalized)
    if cached is not None:
        return cached

    try:
        result = await asyncio.wait_for(
            _fetch_price(normalized),
            timeout=_TIMEOUT,
        )
    except asyncio.TimeoutError:
        return {
            "error": True,
            "error_type": "timeout",
            "message": f"Price fetch for {normalized} timed out after {_TIMEOUT:.0f}s.",
            "partial_data": None,
            "suggestion": "Try again in a few seconds.",
        }
    except Exception as e:
        logger.exception(f"Error fetching price for {normalized}")
        return {
            "error": True,
            "error_type": "data_unavailable",
            "message": f"Failed to fetch price data for {normalized}: {e}",
            "partial_data": None,
            "suggestion": "Try again later. The data source may be temporarily unavailable.",
        }

    if not result.get("error"):
        cache.set("get_stock_price", normalized, result, TTLCache.TTL_PRICE)
    return result


async def _fetch_price(normalized: str) -> dict:
    """Inner coroutine — fetch price from yfinance with fast_info fallback."""
    yf_ticker = to_yfinance_ticker(normalized)
    stock = yf.Ticker(yf_ticker)

    # Primary: full .info (rich metadata)
    info = await asyncio.to_thread(lambda: stock.info) or {}
    price = _coerce(info.get("regularMarketPrice") or info.get("currentPrice"))

    # Fallback: fast_info (lightweight endpoint, fewer fields)
    # FIX: capture the price here and do NOT re-fetch it below
    used_fallback = False
    if price is None:
        used_fallback = True
        try:
            fi = await asyncio.to_thread(lambda: stock.fast_info)
            price = _coerce(getattr(fi, "last_price", None) or getattr(fi, "lastPrice", None))
        except Exception:
            fi = None

        if price is None:
            return {
                "error": True,
                "error_type": "data_unavailable",
                "message": f"No price data available for {normalized}. The ticker may be invalid or delisted.",
                "partial_data": None,
                "suggestion": f"Verify that {normalized} is a valid IDX ticker symbol.",
            }

    # At this point `price` is guaranteed non-None (from either source).
    #
    # The quote endpoint that backs `.info` needs an authenticated crumb and is
    # rate-limited independently of the chart endpoint. When it refuses, `info`
    # comes back empty, `fast_info` still supplies a price, and every other
    # field below silently becomes None. Reporting that as an ordinary quote
    # makes a degraded response indistinguishable from a complete one, so the
    # payload says which fields are actually missing.
    prev_close  = _coerce(info.get("regularMarketPreviousClose") or info.get("previousClose"))
    open_price  = _coerce(info.get("regularMarketOpen")  or info.get("open"))
    high        = _coerce(info.get("regularMarketDayHigh") or info.get("dayHigh"))
    low         = _coerce(info.get("regularMarketDayLow")  or info.get("dayLow"))
    volume      = _coerce(info.get("regularMarketVolume")  or info.get("volume")) or 0.0
    market_cap  = _coerce(info.get("marketCap"))

    change     = safe_round(price - prev_close, 2) if prev_close is not None else None
    change_pct = safe_round((change / prev_close) * 100, 2) if change is not None and prev_close else None
    volume_idr_billion   = safe_round((volume * price) / 1_000_000_000, 2) if volume else None
    market_cap_trillion  = safe_round(market_cap / 1_000_000_000_000, 2) if market_cap else None

    week_52_high = _coerce(info.get("fiftyTwoWeekHigh"))
    week_52_low = _coerce(info.get("fiftyTwoWeekLow"))

    payload = {
        "ticker": normalized,
        "name": info.get("longName") or info.get("shortName") or normalized,
        "price": price,
        "currency": "IDR",
        "change": change,
        "change_percent": change_pct,
        "open": open_price,
        "high": high,
        "low": low,
        "volume": int(volume) if volume else 0,
        "volume_idr_billion": volume_idr_billion,
        "prev_close": prev_close,
        "week_52_high": week_52_high,
        "week_52_low":  week_52_low,
        "market_cap_trillion_idr": market_cap_trillion,
        "market_status": get_market_status(),
        "last_updated": format_wib_iso(),
        # A 401 from the quote endpoint still returns a dict, just without the
        # quote fields, so the presence of `info` says nothing about whether the
        # price itself came from the fallback.
        "source": "yfinance:fast_info" if used_fallback else "yfinance",
    }

    missing = [k for k in ("prev_close", "open", "high", "low",
                           "week_52_high", "week_52_low", "market_cap_trillion_idr")
               if payload[k] is None]
    payload["partial"] = bool(missing)
    if missing:
        payload["missing_fields"] = missing
        payload["partial_reason"] = (
            "Yahoo's quote endpoint was unavailable (auth or rate limit); price came "
            "from the lightweight fallback. Treat only `price` as reliable here."
        )
    return payload


def _coerce(val) -> float | None:
    """Convert a value to float, returning None for None / NaN / non-numeric."""
    if val is None:
        return None
    try:
        f = float(val)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None
