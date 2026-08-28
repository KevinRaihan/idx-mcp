"""Volatility Squeeze Scanner.

Finds stocks where the Bollinger Bands are extremely tight (low volatility)
combined with rising MACD momentum (predicting an imminent explosive breakout).
"""

import asyncio
import logging

import numpy as np
import pandas as pd

from ..utils.cache import cache
from ..utils.formatting import safe_round
from ._scan_common import build_envelope, elapsed_since, log_ticker_failure, scan_timer
from .scanner import _download_batch, _f, _load_tickers, _to_jk

logger = logging.getLogger("idx-mcp.tools.vol_squeeze")

STRATEGY = "volatility_squeeze"
DEFAULT_MIN_VOLUME = 1_000_000
DEFAULT_SQUEEZE_TOLERANCE = 1.10  # within 10% of the 6-month minimum bandwidth
SQUEEZE_LOOKBACK = 125            # ~6 months of trading days
BATCH_SIZE = 80
SCAN_PERIOD = "1y"
MIN_ROWS = 150
_TTL_SCAN = 14_400
_T_SCAN = 150.0


def _ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False).mean()


def _enrich_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    sma20 = df["Close"].rolling(20).mean()
    std20 = df["Close"].rolling(20).std()
    df["bb_width"] = (std20 * 2) / sma20.replace(0, np.nan)

    # Minimum bandwidth over the trailing ~6 months, current bar included.
    df["bb_min_125d"] = df["bb_width"].rolling(SQUEEZE_LOOKBACK).min()

    ema_12 = _ema(df["Close"], 12)
    ema_26 = _ema(df["Close"], 26)
    df["macd_line"] = ema_12 - ema_26
    df["macd_signal"] = _ema(df["macd_line"], 9)
    df["macd_hist"] = df["macd_line"] - df["macd_signal"]

    df["vol_20d_avg"] = df["Volume"].rolling(20).mean()
    return df


def _passes_entry_filters(
    row: pd.Series, prev_row: pd.Series, min_vol: int, tolerance: float
) -> bool:
    close = _f(row.get("Close"))
    bb_width = _f(row.get("bb_width"))
    bb_min = _f(row.get("bb_min_125d"))
    vol = _f(row.get("Volume"))
    macd_hist = _f(row.get("macd_hist"))
    macd_prev = _f(prev_row.get("macd_hist"))

    # A missing previous histogram means momentum direction is unknown, which is
    # the whole point of the filter — reject rather than assume zero.
    if None in (close, bb_width, bb_min, vol, macd_hist, macd_prev):
        return False
    if bb_min <= 0:
        return False

    return (
        bb_width <= bb_min * tolerance   # bandwidth at 6-month lows
        and macd_hist > macd_prev        # momentum turning up
        and vol >= min_vol
    )


def _build_signal(
    ticker_clean: str, df: pd.DataFrame, min_vol: int, tolerance: float
) -> dict | None:
    if df is None or len(df) < MIN_ROWS:
        return None
    row, prev_row = df.iloc[-1], df.iloc[-2]
    if not _passes_entry_filters(row, prev_row, min_vol, tolerance):
        return None

    close = _f(row.get("Close"))
    bb_width = _f(row.get("bb_width"))
    macd_hist = _f(row.get("macd_hist"))
    macd_prev = _f(prev_row.get("macd_hist"))
    vol = _f(row.get("Volume")) or 0.0
    vol_avg = _f(row.get("vol_20d_avg"))
    vol_ratio = (vol / vol_avg) if vol_avg else 0.0

    score = 0.0
    score += 40 if bb_width < 0.05 else 25 if bb_width < 0.10 else 10
    score += 30 if macd_hist > 0 else 15
    score += 30 if vol_ratio > 1.5 else 15 if vol_ratio > 1.0 else 5

    return {
        "ticker": ticker_clean,
        "close": close,
        "bb_width": safe_round(bb_width, 4),
        "bb_width_6mo_min": safe_round(_f(row.get("bb_min_125d")), 4),
        "macd_hist": safe_round(macd_hist, 4),
        "macd_hist_prev": safe_round(macd_prev, 4),
        "macd_above_zero": macd_hist > 0,
        "volume": int(vol),
        "volume_ratio": safe_round(vol_ratio, 2),
        "confidence_score": min(score, 100.0),
    }


def _run_full_scan(min_vol: int, tolerance: float, top_n: int = 10) -> dict:
    started = scan_timer()
    tickers = _load_tickers()
    jk_list = [_to_jk(t) for t in tickers]

    all_data: dict[str, pd.DataFrame] = {}
    for i in range(0, len(jk_list), BATCH_SIZE):
        all_data.update(_download_batch(jk_list[i : i + BATCH_SIZE], period=SCAN_PERIOD))

    signals = []
    for ticker_clean, df in all_data.items():
        try:
            signal = _build_signal(ticker_clean, _enrich_df(df), min_vol, tolerance)
            if signal:
                signals.append(signal)
        except Exception as e:
            log_ticker_failure(logger, ticker_clean, e)

    signals.sort(key=lambda x: x["confidence_score"], reverse=True)
    logger.info(
        "vol_squeeze scan: %d/%d tickers with data, %d signals",
        len(all_data), len(jk_list), len(signals),
    )

    return build_envelope(
        strategy=STRATEGY,
        signals=signals,
        total_scanned=len(jk_list),
        downloaded=len(all_data),
        failed=len(jk_list) - len(all_data),
        filters={"min_volume": min_vol, "squeeze_tolerance": tolerance,
                 "squeeze_lookback_days": SQUEEZE_LOOKBACK},
        elapsed_s=elapsed_since(started),
        top_n=top_n,
    )


async def scan_volatility_squeeze(
    min_volume: int = DEFAULT_MIN_VOLUME,
    squeeze_tolerance: float = DEFAULT_SQUEEZE_TOLERANCE,
) -> dict:
    """Run Volatility Squeeze scan across the BEI universe."""
    try:
        min_volume = int(min_volume)
        squeeze_tolerance = float(squeeze_tolerance)
    except (TypeError, ValueError):
        return {"error": True, "message": "Scan parameters must be numeric."}
    if squeeze_tolerance < 1.0:
        return {"error": True, "message": "squeeze_tolerance must be >= 1.0."}

    cache_key = f"vs_{min_volume}_{squeeze_tolerance}"
    cached = cache.get(STRATEGY, cache_key)
    if cached is not None:
        return cached

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_run_full_scan, min_volume, squeeze_tolerance, 10),
            timeout=_T_SCAN,
        )
    except asyncio.TimeoutError:
        return {
            "error": True,
            "error_type": "timeout",
            "message": f"Volatility squeeze scan exceeded {_T_SCAN:.0f}s.",
            "suggestion": "Retry — upstream price data was slow. Results cache for 4h once complete.",
        }
    except Exception as e:
        logger.exception("vol_squeeze scan failed")
        return {"error": True, "error_type": "scan_failed", "message": str(e)}

    cache.set(STRATEGY, cache_key, result, _TTL_SCAN)
    return result
