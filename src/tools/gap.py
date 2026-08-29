"""Gap Scanner — opening gaps and whether they held.

Gaps behave differently on IDX than on markets without auto-rejection. The
exchange caps how far a stock may move from its reference price in a session
(ARA on the upside, ARB on the downside), so a gap here is not just a sentiment
signal: it is a move against a ceiling. A stock that gaps 8% into a 25% band has
room; the same gap in a 20% band on an expensive stock has less. Each signal
reports how much of the band the gap consumed.

Two directions, and they are not mirror images:

* ``up`` looks for a gap that held — price opened above yesterday's close and
  never traded back down through it. An unfilled gap is the interesting one;
  a gap that fills the same session is a failed move, not a pending one.
* ``down`` looks for the opposite of continuation — a gap down that closed above
  its open, which is the exhaustion candle rather than the breakdown.

One caveat while the market is open: the in-progress session is dropped upstream
by ``drop_incomplete_bars`` because its OHLC is still NaN, so this reads the last
*completed* session's gap. Intraday gap-and-go on today's open is not something
daily bars can answer.
"""

import asyncio
import logging

import pandas as pd

from ..utils.cache import cache
from ..utils.formatting import safe_round
from ._scan_common import (
    Funnel,
    build_envelope,
    elapsed_since,
    log_ticker_failure,
    scan_timer,
)
from .scanner import _f
from .universe import load_universe, universe_size

logger = logging.getLogger("idx-mcp.tools.gap")

STRATEGY = "gap"
DEFAULT_MIN_VOLUME = 500_000
DEFAULT_MIN_GAP_PCT = 2.0
DEFAULT_DIRECTION = "up"
VALID_DIRECTIONS = ("up", "down", "both")

SCAN_PERIOD = "6mo"
MIN_ROWS = 30
_TTL_SCAN = 14_400
_T_SCAN = 150.0


def auto_reject_pct(price: float) -> float:
    """IDX auto-rejection band for a given reference price, in percent.

    Tiers follow the symmetric ARA/ARB regime. IDX revises these by decree, so
    treat the number as context for sizing the move rather than as a tradable
    limit.
    """
    if price < 200:
        return 35.0
    if price <= 5_000:
        return 25.0
    return 20.0


def _enrich_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["prev_close"] = df["Close"].shift(1)
    df["vol_20d_avg"] = df["Volume"].shift(1).rolling(20).mean()
    return df


FUNNEL_STAGES = (
    "enough_history",
    "passed_volume_floor",
    "gap_size_met",
    "gap_behaviour_confirmed",
)


def _build_signal(
    ticker_clean: str,
    df: pd.DataFrame,
    min_vol: int,
    min_gap: float,
    direction: str,
    funnel: Funnel | None = None,
) -> dict | None:
    if df is None or len(df) < MIN_ROWS:
        return None
    if funnel:
        funnel.passed("enough_history")

    row = df.iloc[-1]
    open_, high, low, close = (_f(row.get(c)) for c in ("Open", "High", "Low", "Close"))
    prev_close = _f(row.get("prev_close"))
    vol = _f(row.get("Volume"))
    vol_avg = _f(row.get("vol_20d_avg"))

    if None in (open_, high, low, close, prev_close, vol) or prev_close <= 0:
        return None
    if vol < min_vol:
        return None
    if funnel:
        funnel.passed("passed_volume_floor")

    gap_pct = (open_ - prev_close) / prev_close * 100.0
    vol_ratio = (vol / vol_avg) if vol_avg else 0.0
    band_pct = auto_reject_pct(prev_close)
    gap_share_of_band = abs(gap_pct) / band_pct * 100.0
    day_change_pct = (close - prev_close) / prev_close * 100.0

    gap_direction: str
    score = 0.0

    if gap_pct >= min_gap and direction in ("up", "both"):
        gap_direction = "up"
        if funnel:
            funnel.passed("gap_size_met")
        # Unfilled means the session low never traded back to yesterday's close.
        unfilled = low > prev_close
        held = close >= open_
        if close <= prev_close:
            return None  # gave the whole gap back; not a continuation setup
        if funnel:
            funnel.passed("gap_behaviour_confirmed")

        score += 30 if unfilled else 10
        score += 25 if held else 12
        score += 25 if vol_ratio >= 2 else 15 if vol_ratio >= 1.2 else 5
        # A modest gap leaves room under the band and a workable stop.
        score += 20 if gap_share_of_band <= 25 else 12 if gap_share_of_band <= 50 else 5

    elif gap_pct <= -min_gap and direction in ("down", "both"):
        gap_direction = "down"
        if funnel:
            funnel.passed("gap_size_met")
        unfilled = high < prev_close
        # Exhaustion, not breakdown: the session had to close above its open.
        held = close > open_
        if not held:
            return None
        if funnel:
            funnel.passed("gap_behaviour_confirmed")

        recovery_pct = (close - low) / low * 100.0 if low > 0 else 0.0
        score += 35
        score += 25 if recovery_pct >= 3 else 15 if recovery_pct >= 1 else 5
        score += 25 if vol_ratio >= 2 else 15 if vol_ratio >= 1.2 else 5
        score += 15 if close > prev_close else 5
    else:
        return None

    return {
        "ticker": ticker_clean,
        "close": close,
        "open": open_,
        "high": high,
        "low": low,
        "prev_close": safe_round(prev_close, 2),
        "gap_pct": safe_round(gap_pct, 2),
        "gap_direction": gap_direction,
        "day_change_pct": safe_round(day_change_pct, 2),
        "gap_unfilled": bool(unfilled),
        "held_gap": bool(held),
        "auto_reject_band_pct": band_pct,
        "gap_share_of_band_pct": safe_round(gap_share_of_band, 1),
        "volume": int(vol),
        "volume_ratio": safe_round(vol_ratio, 2),
        "bar_date": str(df.index[-1].date()) if hasattr(df.index[-1], "date") else None,
        "confidence_score": min(score, 100.0),
    }


def _run_full_scan(min_vol: int, min_gap: float, direction: str, top_n: int = 10) -> dict:
    started = scan_timer()
    all_data = load_universe(period=SCAN_PERIOD)
    funnel = Funnel(*FUNNEL_STAGES)

    signals = []
    for ticker_clean, df in all_data.items():
        try:
            signal = _build_signal(
                ticker_clean, _enrich_df(df), min_vol, min_gap, direction, funnel
            )
            if signal:
                signals.append(signal)
        except Exception as e:
            log_ticker_failure(logger, ticker_clean, e)

    signals.sort(key=lambda x: x["confidence_score"], reverse=True)
    total = universe_size()
    logger.info(
        "gap scan (%s): %d/%d tickers with data, %d signals",
        direction, len(all_data), total, len(signals),
    )

    envelope = build_envelope(
        strategy=STRATEGY,
        signals=signals,
        total_scanned=total,
        downloaded=len(all_data),
        failed=total - len(all_data),
        filters={"min_volume": min_vol, "min_gap_pct": min_gap, "direction": direction},
        elapsed_s=elapsed_since(started),
        top_n=top_n,
        funnel=funnel,
    )
    envelope["note"] = (
        "Reads the last completed daily bar. While the market is open, the "
        "in-progress session is excluded, so this reflects the previous session's gap."
    )
    return envelope


async def scan_gap(
    min_volume: int = DEFAULT_MIN_VOLUME,
    min_gap_pct: float = DEFAULT_MIN_GAP_PCT,
    direction: str = DEFAULT_DIRECTION,
) -> dict:
    """Find opening gaps that held, or gap-downs that reversed, across the BEI universe."""
    try:
        min_volume = int(min_volume)
        min_gap_pct = float(min_gap_pct)
    except (TypeError, ValueError):
        return {"error": True, "message": "Scan parameters must be numeric."}
    if min_gap_pct <= 0:
        return {"error": True, "message": "min_gap_pct must be positive."}

    direction = str(direction).lower().strip()
    if direction not in VALID_DIRECTIONS:
        return {
            "error": True,
            "message": f"direction must be one of {', '.join(VALID_DIRECTIONS)}.",
        }

    cache_key = f"gap_{min_volume}_{min_gap_pct}_{direction}"
    cached = cache.get(STRATEGY, cache_key)
    if cached is not None:
        return cached

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_run_full_scan, min_volume, min_gap_pct, direction, 10),
            timeout=_T_SCAN,
        )
    except asyncio.TimeoutError:
        return {
            "error": True,
            "error_type": "timeout",
            "message": f"Gap scan exceeded {_T_SCAN:.0f}s.",
            "suggestion": "Retry — upstream price data was slow. Results cache for 4h once complete.",
        }
    except Exception as e:
        logger.exception("gap scan failed")
        return {"error": True, "error_type": "scan_failed", "message": str(e)}

    cache.set(STRATEGY, cache_key, result, _TTL_SCAN)
    return result
