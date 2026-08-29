"""Trend Pullback Scanner — buying a dip inside a confirmed uptrend.

The gap this fills sits between two existing scanners. ``scan_mean_reversion``
finds oversold stocks with no trend requirement at all, so it happily returns
things in freefall. ``scan_golden_cross`` requires the SMA50/SMA200 cross to be
recent, so it sees a trend exactly once and misses every dip that follows for
the rest of that trend's life.

This scan wants the boring middle: a stock already in an established uptrend
that has pulled back to its short moving average without breaking structure.
Entry conditions are deliberately unexciting — RSI in the 40s, not the 20s. A
stock in a real uptrend rarely gets deeply oversold, so demanding RSI < 30 as a
dip-buy filter selects against the trends worth buying.

Structure is the check that separates a pullback from the start of a decline:
the 20-day low must sit above the 60-day low. Once price takes out the prior
swing low, it is a downtrend with good moving averages, not a dip.
"""

import asyncio
import logging

import numpy as np
import pandas as pd

from ..utils.cache import cache
from ..utils.formatting import safe_round
from ._scan_common import build_envelope, elapsed_since, log_ticker_failure, scan_timer
from .mean_reversion import _compute_rsi
from .scanner import _f
from .universe import load_universe, universe_size

logger = logging.getLogger("idx-mcp.tools.trend_pullback")

STRATEGY = "trend_pullback"
DEFAULT_MIN_VOLUME = 500_000
DEFAULT_RSI_MIN = 40.0
DEFAULT_RSI_MAX = 58.0
DEFAULT_MAX_PULLBACK_PCT = 15.0
MIN_PULLBACK_PCT = 2.0          # anything shallower is not a pullback, just noise
SMA50_BREAK_TOLERANCE = 0.97    # 3% below SMA50 still counts as intact
HIGH_LOOKBACK = 60
STRUCTURE_SHORT, STRUCTURE_LONG = 20, 60

SCAN_PERIOD = "2y"
MIN_ROWS = 210                  # SMA200 needs its full lookback
_TTL_SCAN = 14_400
_T_SCAN = 150.0


def _enrich_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["SMA20"] = df["Close"].rolling(20).mean()
    df["SMA50"] = df["Close"].rolling(50).mean()
    df["SMA200"] = df["Close"].rolling(200).mean()
    df["rsi"] = _compute_rsi(df["Close"])
    df["vol_20d_avg"] = df["Volume"].shift(1).rolling(20).mean()
    df["high_60d"] = df["High"].rolling(HIGH_LOOKBACK).max()
    df["low_20d"] = df["Low"].rolling(STRUCTURE_SHORT).min()
    df["low_60d"] = df["Low"].rolling(STRUCTURE_LONG).min()
    return df


def _build_signal(
    ticker_clean: str,
    df: pd.DataFrame,
    min_vol: int,
    rsi_min: float,
    rsi_max: float,
    max_pullback: float,
) -> dict | None:
    if df is None or len(df) < MIN_ROWS:
        return None

    row = df.iloc[-1]
    close = _f(row.get("Close"))
    sma20, sma50, sma200 = (_f(row.get(c)) for c in ("SMA20", "SMA50", "SMA200"))
    rsi = _f(row.get("rsi"))
    vol = _f(row.get("Volume"))
    high_60d = _f(row.get("high_60d"))
    low_20d, low_60d = _f(row.get("low_20d")), _f(row.get("low_60d"))

    if None in (close, sma20, sma50, sma200, rsi, vol, high_60d, low_20d, low_60d):
        return None
    if vol < min_vol or high_60d <= 0 or sma200 <= 0:
        return None

    # 1. The trend must already exist.
    if not (close > sma200 and sma50 > sma200):
        return None

    # 2. Price has actually pulled back, but has not broken the medium MA.
    if close > sma20:
        return None
    if close < sma50 * SMA50_BREAK_TOLERANCE:
        return None

    # 3. Depth of the pullback from the recent swing high.
    pullback_pct = (high_60d - close) / high_60d * 100.0
    if not (MIN_PULLBACK_PCT <= pullback_pct <= max_pullback):
        return None

    # 4. Momentum cooled without collapsing.
    if not (rsi_min <= rsi <= rsi_max):
        return None

    # 5. Structure intact: the recent low has held above the older swing low.
    structure_intact = low_20d > low_60d
    if not structure_intact:
        return None

    above_sma200_pct = (close - sma200) / sma200 * 100.0
    vol_avg = _f(row.get("vol_20d_avg"))
    vol_ratio = (vol / vol_avg) if vol_avg else 0.0

    score = 0.0
    # A pullback in the 4-10% band is the classic entry; deeper starts to
    # question the trend, shallower gives no edge on price.
    score += 35 if 4 <= pullback_pct <= 10 else 22 if pullback_pct < 4 else 12
    score += 25 if 45 <= rsi <= 55 else 15
    score += 20 if above_sma200_pct >= 10 else 12 if above_sma200_pct >= 3 else 6
    # Drying-up volume into a pullback is the constructive version.
    score += 20 if vol_ratio < 0.8 else 12 if vol_ratio < 1.2 else 5

    return {
        "ticker": ticker_clean,
        "close": close,
        "sma20": safe_round(sma20, 2),
        "sma50": safe_round(sma50, 2),
        "sma200": safe_round(sma200, 2),
        "above_sma200_pct": safe_round(above_sma200_pct, 2),
        "high_60d": safe_round(high_60d, 2),
        "pullback_from_high_pct": safe_round(pullback_pct, 2),
        "rsi": safe_round(rsi, 2),
        "low_20d": safe_round(low_20d, 2),
        "low_60d": safe_round(low_60d, 2),
        "structure_intact": structure_intact,
        "volume": int(vol),
        "volume_ratio": safe_round(vol_ratio, 2),
        "confidence_score": min(score, 100.0),
    }


def _run_full_scan(
    min_vol: int, rsi_min: float, rsi_max: float, max_pullback: float, top_n: int = 10
) -> dict:
    started = scan_timer()
    all_data = load_universe(period=SCAN_PERIOD)

    signals = []
    for ticker_clean, df in all_data.items():
        try:
            signal = _build_signal(
                ticker_clean, _enrich_df(df), min_vol, rsi_min, rsi_max, max_pullback
            )
            if signal:
                signals.append(signal)
        except Exception as e:
            log_ticker_failure(logger, ticker_clean, e)

    signals.sort(key=lambda x: x["confidence_score"], reverse=True)
    total = universe_size()
    logger.info(
        "trend_pullback scan: %d/%d tickers with data, %d signals",
        len(all_data), total, len(signals),
    )

    return build_envelope(
        strategy=STRATEGY,
        signals=signals,
        total_scanned=total,
        downloaded=len(all_data),
        failed=total - len(all_data),
        filters={"min_volume": min_vol, "rsi_min": rsi_min, "rsi_max": rsi_max,
                 "max_pullback_pct": max_pullback, "min_pullback_pct": MIN_PULLBACK_PCT},
        elapsed_s=elapsed_since(started),
        top_n=top_n,
    )


async def scan_trend_pullback(
    min_volume: int = DEFAULT_MIN_VOLUME,
    rsi_min: float = DEFAULT_RSI_MIN,
    rsi_max: float = DEFAULT_RSI_MAX,
    max_pullback_pct: float = DEFAULT_MAX_PULLBACK_PCT,
) -> dict:
    """Find dips inside confirmed uptrends across the BEI universe."""
    try:
        min_volume = int(min_volume)
        rsi_min = float(rsi_min)
        rsi_max = float(rsi_max)
        max_pullback_pct = float(max_pullback_pct)
    except (TypeError, ValueError):
        return {"error": True, "message": "Scan parameters must be numeric."}
    if rsi_min >= rsi_max:
        return {"error": True, "message": "rsi_min must be less than rsi_max."}
    if max_pullback_pct <= MIN_PULLBACK_PCT:
        return {"error": True,
                "message": f"max_pullback_pct must exceed {MIN_PULLBACK_PCT}."}

    cache_key = f"tp_{min_volume}_{rsi_min}_{rsi_max}_{max_pullback_pct}"
    cached = cache.get(STRATEGY, cache_key)
    if cached is not None:
        return cached

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                _run_full_scan, min_volume, rsi_min, rsi_max, max_pullback_pct, 10
            ),
            timeout=_T_SCAN,
        )
    except asyncio.TimeoutError:
        return {
            "error": True,
            "error_type": "timeout",
            "message": f"Trend pullback scan exceeded {_T_SCAN:.0f}s.",
            "suggestion": "Retry — upstream price data was slow. Results cache for 4h once complete.",
        }
    except Exception as e:
        logger.exception("trend_pullback scan failed")
        return {"error": True, "error_type": "scan_failed", "message": str(e)}

    cache.set(STRATEGY, cache_key, result, _TTL_SCAN)
    return result
