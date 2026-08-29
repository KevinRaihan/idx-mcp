"""Breakout-to-New-High Scanner (Darvas box).

``scan_ma_breakout`` finds compression in the moving averages, which is one
specific route into a breakout. A stock that spent three months in a flat range
and then closed above the top of it never has to compress its MAs to do so, and
that setup was previously unfindable anywhere in this package.

The base range test is what keeps this from returning every stock that ticked up
today. A breakout out of a tight multi-month base is a different event from a
breakout out of a wide, choppy one: the tight base means holders agreed on price
for a long time, so there is little overhead supply above.

The prior high excludes the current bar. Comparing today's close to a window
that already contains today's high makes the test nearly tautological — the
close would only have to beat its own session high.
"""

import asyncio
import logging

import pandas as pd

from ..utils.cache import cache
from ..utils.formatting import safe_round
from ._scan_common import build_envelope, elapsed_since, log_ticker_failure, scan_timer
from .scanner import _f
from .universe import load_universe, universe_size

logger = logging.getLogger("idx-mcp.tools.breakout_high")

STRATEGY = "breakout_high"
DEFAULT_MIN_VOLUME = 500_000
DEFAULT_LOOKBACK_DAYS = 60
DEFAULT_VOL_MULTIPLE = 1.5
DEFAULT_MAX_BASE_RANGE_PCT = 25.0
WEEK_52_BARS = 252

SCAN_PERIOD = "2y"
MIN_ROWS = 90
_TTL_SCAN = 14_400
_T_SCAN = 150.0


def _enrich_df(df: pd.DataFrame, lookback: int) -> pd.DataFrame:
    df = df.copy()
    # shift(1) everywhere: the base is the window *before* the breakout bar.
    prior_high = df["High"].shift(1).rolling(lookback)
    prior_low = df["Low"].shift(1).rolling(lookback)
    df["prior_high"] = prior_high.max()
    df["prior_low"] = prior_low.min()
    df["base_mid"] = df["Close"].shift(1).rolling(lookback).mean()
    df["vol_20d_avg"] = df["Volume"].shift(1).rolling(20).mean()
    df["high_52w"] = df["High"].shift(1).rolling(WEEK_52_BARS, min_periods=60).max()
    return df


def _new_funnel() -> dict[str, int]:
    """Survivor counts per stage, so an empty scan can explain itself."""
    return {
        "enough_history": 0,
        "passed_volume_floor": 0,
        "closed_above_prior_high": 0,
        "base_tight_enough": 0,
        "volume_confirmed": 0,
    }


def _build_signal(
    ticker_clean: str,
    df: pd.DataFrame,
    min_vol: int,
    vol_multiple: float,
    max_base_range: float,
    funnel: dict[str, int] | None = None,
) -> dict | None:
    def tally(stage: str) -> None:
        if funnel is not None:
            funnel[stage] += 1

    if df is None or len(df) < MIN_ROWS:
        return None
    tally("enough_history")

    row = df.iloc[-1]
    close = _f(row.get("Close"))
    vol = _f(row.get("Volume"))
    prior_high, prior_low = _f(row.get("prior_high")), _f(row.get("prior_low"))
    base_mid = _f(row.get("base_mid"))
    vol_avg = _f(row.get("vol_20d_avg"))

    if None in (close, vol, prior_high, prior_low, base_mid) or base_mid <= 0:
        return None
    if vol < min_vol or prior_high <= 0:
        return None
    tally("passed_volume_floor")

    # 1. The breakout itself.
    if close < prior_high:
        return None
    tally("closed_above_prior_high")

    # 2. The base it broke out of must have been tight.
    base_range_pct = (prior_high - prior_low) / base_mid * 100.0
    if base_range_pct > max_base_range:
        return None
    tally("base_tight_enough")

    # 3. Volume has to confirm. A breakout on no volume is a quote, not a move.
    if not vol_avg or vol_avg <= 0:
        return None
    vol_ratio = vol / vol_avg
    if vol_ratio < vol_multiple:
        return None
    tally("volume_confirmed")

    breakout_margin_pct = (close - prior_high) / prior_high * 100.0
    high_52w = _f(row.get("high_52w"))
    is_52w_high = bool(high_52w and close >= high_52w)
    dist_to_52w_pct = ((high_52w - close) / high_52w * 100.0) if high_52w else None

    score = 0.0
    score += 35 if base_range_pct <= 10 else 25 if base_range_pct <= 18 else 15
    score += 30 if vol_ratio >= 3 else 22 if vol_ratio >= 2 else 14
    score += 20 if is_52w_high else 10
    # A huge gap past the base is a worse entry, not a better one: the stop
    # sits back at the base and the risk has already widened.
    score += 15 if breakout_margin_pct <= 3 else 8 if breakout_margin_pct <= 7 else 3

    return {
        "ticker": ticker_clean,
        "close": close,
        "prior_high": safe_round(prior_high, 2),
        "prior_low": safe_round(prior_low, 2),
        "breakout_margin_pct": safe_round(breakout_margin_pct, 2),
        "base_range_pct": safe_round(base_range_pct, 2),
        "high_52w": safe_round(high_52w, 2),
        "is_52w_high": is_52w_high,
        "distance_to_52w_high_pct": safe_round(dist_to_52w_pct, 2),
        "volume": int(vol),
        "volume_ratio": safe_round(vol_ratio, 2),
        "confidence_score": min(score, 100.0),
    }


def _run_full_scan(
    min_vol: int, lookback: int, vol_multiple: float, max_base_range: float, top_n: int = 10
) -> dict:
    started = scan_timer()
    all_data = load_universe(period=SCAN_PERIOD)
    funnel = _new_funnel()

    signals = []
    for ticker_clean, df in all_data.items():
        try:
            signal = _build_signal(
                ticker_clean, _enrich_df(df, lookback), min_vol, vol_multiple,
                max_base_range, funnel,
            )
            if signal:
                signals.append(signal)
        except Exception as e:
            log_ticker_failure(logger, ticker_clean, e)

    signals.sort(key=lambda x: x["confidence_score"], reverse=True)
    total = universe_size()
    logger.info(
        "breakout_high scan: %d/%d tickers with data, %d signals",
        len(all_data), total, len(signals),
    )

    envelope = build_envelope(
        strategy=STRATEGY,
        signals=signals,
        total_scanned=total,
        downloaded=len(all_data),
        failed=total - len(all_data),
        filters={"min_volume": min_vol, "lookback_days": lookback,
                 "vol_multiple": vol_multiple, "max_base_range_pct": max_base_range},
        elapsed_s=elapsed_since(started),
        top_n=top_n,
        funnel=funnel,
    )
    if not signals:
        envelope["note"] = (
            "No signals is a normal reading for this strategy: a stock must be at a "
            "multi-month high, out of a tight base, on heavy volume, and few are on any "
            "given day. Read filter_funnel to see which stage emptied — if "
            "closed_above_prior_high is near zero the market simply has no breakouts, "
            "which is different from the filters being too strict."
        )
    return envelope


async def scan_breakout_high(
    min_volume: int = DEFAULT_MIN_VOLUME,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    vol_multiple: float = DEFAULT_VOL_MULTIPLE,
    max_base_range_pct: float = DEFAULT_MAX_BASE_RANGE_PCT,
) -> dict:
    """Find stocks closing above a tight multi-month base on confirming volume."""
    try:
        min_volume = int(min_volume)
        lookback_days = int(lookback_days)
        vol_multiple = float(vol_multiple)
        max_base_range_pct = float(max_base_range_pct)
    except (TypeError, ValueError):
        return {"error": True, "message": "Scan parameters must be numeric."}
    if not 10 <= lookback_days <= 250:
        return {"error": True, "message": "lookback_days must be between 10 and 250."}
    if vol_multiple <= 0 or max_base_range_pct <= 0:
        return {"error": True,
                "message": "vol_multiple and max_base_range_pct must be positive."}

    cache_key = f"bh_{min_volume}_{lookback_days}_{vol_multiple}_{max_base_range_pct}"
    cached = cache.get(STRATEGY, cache_key)
    if cached is not None:
        return cached

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                _run_full_scan, min_volume, lookback_days, vol_multiple,
                max_base_range_pct, 10,
            ),
            timeout=_T_SCAN,
        )
    except asyncio.TimeoutError:
        return {
            "error": True,
            "error_type": "timeout",
            "message": f"Breakout scan exceeded {_T_SCAN:.0f}s.",
            "suggestion": "Retry — upstream price data was slow. Results cache for 4h once complete.",
        }
    except Exception as e:
        logger.exception("breakout_high scan failed")
        return {"error": True, "error_type": "scan_failed", "message": str(e)}

    cache.set(STRATEGY, cache_key, result, _TTL_SCAN)
    return result
