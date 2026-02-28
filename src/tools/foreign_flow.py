"""get_foreign_flow tool — Net foreign buy/sell flow."""

import asyncio
import logging

from ..scrapers.stockbit import scrape_foreign_flow
from ..utils.cache import TTLCache, cache
from ..utils.formatting import format_net_flow
from ..utils.ticker import validate_ticker
from ..utils.time_utils import now_wib

logger = logging.getLogger("idx-mcp.tools.foreign_flow")

_TIMEOUT = 20.0


async def get_foreign_flow(ticker: str | None = None, period: str = "daily") -> dict:
    """Get net foreign buy/sell flow for a stock or the overall market."""
    normalized = None
    if ticker:
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

    period = (period or "daily").lower()
    if period not in ("daily", "weekly", "monthly"):
        period = "daily"

    cache_key = normalized or "MARKET"
    cached = cache.get("get_foreign_flow", cache_key, {"period": period})
    if cached is not None:
        return cached

    try:
        data = await asyncio.wait_for(
            scrape_foreign_flow(normalized),
            timeout=_TIMEOUT,
        )
    except asyncio.TimeoutError:
        return {
            "error": True,
            "error_type": "timeout",
            "message": f"Foreign flow fetch timed out after {_TIMEOUT:.0f}s.",
            "partial_data": {"ticker": cache_key, "period": period},
            "suggestion": "Try again in a few seconds.",
        }
    except Exception as e:
        logger.exception(f"Error fetching foreign flow for {cache_key}")
        return {
            "error": True,
            "error_type": "scrape_failed",
            "message": f"Foreign flow data unavailable: {e}",
            "partial_data": {"ticker": cache_key, "period": period},
            "suggestion": "Try again later. The data source may be temporarily unavailable.",
        }

    foreign_buy  = data.get("foreign_buy_idr",  0)
    foreign_sell = data.get("foreign_sell_idr", 0)
    foreign_net  = data.get("foreign_net_idr",  0)
    foreign_net_lot = int(foreign_net / 100_000) if foreign_net else 0

    result = {
        "ticker":               cache_key,
        "period":               period,
        "date":                 now_wib().strftime("%Y-%m-%d"),
        "foreign_buy_idr":      foreign_buy,
        "foreign_sell_idr":     foreign_sell,
        "foreign_net_idr":      foreign_net,
        "foreign_net_formatted": format_net_flow(foreign_net),
        "foreign_net_lot":      foreign_net_lot,
        "trend": {
            "5d_cumulative_net_idr":  None,
            "20d_cumulative_net_idr": None,
            "signal":                 "data_limited",
        },
        "source":      "stockbit.com (scraped)",
        "scrape_note": "Data may be delayed. Cumulative trend requires historical scraping.",
    }

    cache.set("get_foreign_flow", cache_key, result, TTLCache.TTL_FLOW, {"period": period})
    return result
