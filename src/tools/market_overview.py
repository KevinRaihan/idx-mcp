"""get_market_overview tool — IHSG level, sectors, macro snapshot."""

import asyncio
import logging
import math

import yfinance as yf

from ..scrapers.idx import scrape_sector_indices
from ..utils.cache import TTLCache, cache
from ..utils.completeness import mark_partial
from ..utils.formatting import safe_round
from ..utils.ohlcv import drop_incomplete_bars
from ..utils.time_utils import format_wib_iso, get_market_status

logger = logging.getLogger("idx-mcp.tools.market_overview")

# Commodity / macro yfinance tickers
MACRO_TICKERS = {
    "usd_idr":           "USDIDR=X",
    "gold_usd":          "GC=F",
    "nickel_lme_usd":    "NI=F",
    "coal_newcastle_usd": "MTFc1",   # Newcastle coal futures
    "cpo_usd":           "KO=F",    # Palm oil proxy
}

_TIMEOUT = 25.0  # seconds for the full market overview


async def get_market_overview(include_macro: bool = True) -> dict:
    """Get IDX market overview: IHSG level, sectoral indices, macro snapshot."""
    cached = cache.get("get_market_overview", "MARKET", {"include_macro": include_macro})
    if cached is not None:
        return cached

    try:
        result = await asyncio.wait_for(
            _build_overview(include_macro),
            timeout=_TIMEOUT,
        )
    except asyncio.TimeoutError:
        return {
            "error": True,
            "error_type": "timeout",
            "message": f"Market overview timed out after {_TIMEOUT:.0f}s.",
            "partial_data": None,
            "suggestion": "Try again in a few seconds.",
        }
    except Exception as e:
        logger.exception("Error fetching market overview")
        return {
            "error": True,
            "error_type": "data_unavailable",
            "message": f"Failed to fetch market overview: {e}",
            "partial_data": None,
            "suggestion": "Try again later.",
        }

    cache.set("get_market_overview", "MARKET", result, TTLCache.TTL_MARKET, {"include_macro": include_macro})
    return result


async def _build_overview(include_macro: bool) -> dict:
    """Fetch IHSG, sectors, and (optionally) macro data — all concurrently."""
    # Fan out: IHSG + sectors + macro all at once
    ihsg_coro   = asyncio.to_thread(_ihsg_snapshot)
    sector_coro = _safe_scrape_sectors()
    macro_coro  = _fetch_macro_data() if include_macro else _noop()

    ihsg, sectors, macro = await asyncio.gather(
        ihsg_coro, sector_coro, macro_coro,
        return_exceptions=True,
    )

    # IHSG
    if isinstance(ihsg, Exception):
        logger.warning(f"IHSG fetch failed: {ihsg}")
        ihsg = ({}, None)
    ihsg_info, ihsg_history = ihsg

    ihsg_volume     = _coerce(ihsg_info.get("regularMarketVolume")) or 0.0
    volume_trillion = safe_round(ihsg_volume / 1_000_000_000_000, 2) if ihsg_volume else None

    result = {
        "ihsg": {
            **ihsg_level_and_change(ihsg_info, ihsg_history),
            "volume_idr_trillion": volume_trillion,
        },
        "market_status": get_market_status(),
        "timestamp":     format_wib_iso(),
        "sector_performance": sectors if isinstance(sectors, list) else [],
        "source": "yfinance + web scraping",
    }

    if include_macro:
        result["macro"] = macro if isinstance(macro, dict) else {
            "bi_rate_pct": None, "usd_idr": None, "inflation_yoy_pct": None,
            "coal_newcastle_usd": None, "cpo_usd": None, "nickel_lme_usd": None, "gold_usd": None,
        }

    return mark_partial(
        result,
        ("ihsg.value", "ihsg.change_percent", "sector_performance",
         "macro.usd_idr", "macro.bi_rate_pct", "macro.inflation_yoy_pct"),
        "IHSG, sector or macro figures could not be scraped. An empty "
        "sector_performance means the source did not answer, not that no sector "
        "moved.",
    )


def _ihsg_snapshot() -> tuple[dict, object]:
    """IHSG quote fields and recent daily bars, each allowed to fail on its own."""
    ticker = yf.Ticker("^JKSE")
    try:
        info = ticker.info or {}
    except Exception as e:
        logger.warning("IHSG info failed: %s", e)
        info = {}
    try:
        history = ticker.history(period="10d", auto_adjust=False)
    except Exception as e:
        logger.warning("IHSG history failed: %s", e)
        history = None
    return info, history


def ihsg_level_and_change(info: dict, history) -> dict:
    """IHSG level and change, measured against the previous session's close.

    Yahoo's ``regularMarketPreviousClose`` for ^JKSE can lag a session. On
    15-Sep-2026 at 10:28 WIB it still held Friday's 6,541.38 when Monday had
    closed at 6,534.69, and the overview reported -0.99% for a -0.89% morning.
    The daily history carries both closes, so the change is read from its last
    two bars. The quote fields are only a fallback, and the basis says which.
    """
    closes = None
    if history is not None and not getattr(history, "empty", True) and "Close" in history:
        closes = drop_incomplete_bars(history)["Close"]

    session_date = None
    if closes is not None and len(closes) >= 2:
        value, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
        session_date = str(closes.index[-1].date())
        basis = "prior_session_close_from_history"
    else:
        value = _coerce(info.get("regularMarketPrice") or info.get("previousClose"))
        prev = _coerce(info.get("regularMarketPreviousClose") or info.get("previousClose"))
        basis = "yahoo_previous_close_field" if value is not None and prev is not None else None

    change = value - prev if value is not None and prev is not None else None
    change_pct = change / prev * 100 if change is not None and prev else None
    return {
        "value": safe_round(value, 2),
        "change": safe_round(change, 2),
        "change_percent": safe_round(change_pct, 2),
        "previous_close": safe_round(prev, 2),
        "session_date": session_date,
        "change_basis": basis,
    }


async def _safe_scrape_sectors() -> list:
    try:
        return await scrape_sector_indices() or []
    except Exception:
        return []


async def _noop() -> None:
    return None


async def _fetch_macro_data() -> dict:
    """Fetch all macro tickers concurrently (was sequential — now uses gather)."""
    macro: dict = {
        "bi_rate_pct":        None,   # Needs BI website scraping
        "usd_idr":            None,
        "inflation_yoy_pct":  None,   # Needs separate scraping
        "coal_newcastle_usd": None,
        "cpo_usd":            None,
        "nickel_lme_usd":     None,
        "gold_usd":           None,
    }

    async def _one(key: str, symbol: str):
        try:
            # Capture `symbol` by value via default arg
            info = await asyncio.to_thread(lambda s=symbol: yf.Ticker(s).info) or {}
            price = _coerce(info.get("regularMarketPrice") or info.get("previousClose"))
            return key, price
        except Exception as e:
            logger.debug(f"Macro ticker {symbol} failed: {e}")
            return key, None

    # FIX: all 5 tickers fetched concurrently instead of sequentially
    results = await asyncio.gather(*[_one(k, v) for k, v in MACRO_TICKERS.items()])
    for key, val in results:
        if val is not None:
            macro[key] = val

    return macro


def _coerce(val) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None
