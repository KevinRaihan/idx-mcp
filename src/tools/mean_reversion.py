"""Mean Reversion (Deep Oversold) Scanner.

Finds stocks that have deviated significantly below their short-term moving average
(SMA 20) with deeply oversold RSI (< 30) and high relative volume, indicating capitulation.
"""

import asyncio
import logging

import numpy as np
import pandas as pd

from ..utils.cache import cache
from ..utils.formatting import safe_round
from ._scan_common import build_envelope, elapsed_since, log_ticker_failure, scan_timer
from .scanner import _f, _load_tickers, _to_jk
from .universe import load_universe

logger = logging.getLogger("idx-mcp.tools.mean_reversion")

STRATEGY = "mean_reversion"
DEFAULT_RSI_THRESH = 30.0
DEFAULT_MIN_VOLUME = 500_000
DEFAULT_MIN_BELOW_SMA20_PCT = 5.0
BATCH_SIZE = 80
SCAN_PERIOD = "6mo"
MIN_ROWS = 50
_TTL_SCAN = 14_400
_T_SCAN = 150.0


def _compute_rsi(series: pd.Series, length: int = 14) -> pd.Series:
    """Wilder's RSI.

    A window with no down-closes gives avg_loss == 0. Dividing by it would yield
    NaN and silently drop the ticker, so those rows are pinned to RSI 100 (and
    the no-gain case to 0), which is the defined limit.
    """
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))

    flat = (avg_loss == 0) & (avg_gain == 0)
    rsi = rsi.mask((avg_loss == 0) & (avg_gain > 0), 100.0)
    rsi = rsi.mask(flat, 50.0)
    return rsi


def _enrich_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["SMA20"] = df["Close"].rolling(20).mean()
    df["rsi"] = _compute_rsi(df["Close"])
    df["vol_20d_avg"] = df["Volume"].rolling(20).mean()
    return df


def _passes_entry_filters(
    row: pd.Series, rsi_thresh: float, min_vol: int, min_below_pct: float
) -> bool:
    close = _f(row.get("Close"))
    sma20 = _f(row.get("SMA20"))
    rsi = _f(row.get("rsi"))
    vol = _f(row.get("Volume"))

    if None in (close, sma20, rsi, vol):
        return False
    if close <= 0 or sma20 <= 0:
        return False

    return (
        rsi < rsi_thresh
        and close < sma20 * (1.0 - min_below_pct / 100.0)
        and vol >= min_vol
    )


def _build_signal(
    ticker_clean: str,
    df: pd.DataFrame,
    rsi_thresh: float,
    min_vol: int,
    min_below_pct: float,
) -> dict | None:
    if df is None or len(df) < MIN_ROWS:
        return None
    row = df.iloc[-1]
    if not _passes_entry_filters(row, rsi_thresh, min_vol, min_below_pct):
        return None

    close = _f(row.get("Close"))
    sma20 = _f(row.get("SMA20"))
    rsi = _f(row.get("rsi"))
    dist_pct = (sma20 - close) / close * 100

    vol = _f(row.get("Volume")) or 0.0
    vol_avg = _f(row.get("vol_20d_avg"))
    vol_ratio = (vol / vol_avg) if vol_avg else 0.0

    score = 0.0
    score += 40 if rsi < 20 else 25 if rsi < 25 else 15
    score += 40 if dist_pct > 15 else 25 if dist_pct > 10 else 15
    score += 20 if vol_ratio > 2.0 else 10 if vol_ratio > 1.0 else 5

    return {
        "ticker": ticker_clean,
        "close": close,
        "rsi": safe_round(rsi, 1),
        "sma20": safe_round(sma20, 0),
        "distance_below_sma20_pct": safe_round(dist_pct, 2),
        "volume": int(vol),
        "volume_ratio": safe_round(vol_ratio, 2),
        "confidence_score": min(score, 100.0),
    }


def _run_full_scan(
    rsi_thresh: float,
    min_vol: int,
    min_below_pct: float,
    top_n: int = 10,
) -> dict:
    started = scan_timer()
    tickers = _load_tickers()
    jk_list = [_to_jk(t) for t in tickers]

    # One shared universe fetch backs every scanner; see tools/universe.py.
    all_data = load_universe(period=SCAN_PERIOD)

    signals = []
    for ticker_clean, df in all_data.items():
        try:
            signal = _build_signal(
                ticker_clean, _enrich_df(df), rsi_thresh, min_vol, min_below_pct
            )
            if signal:
                signals.append(signal)
        except Exception as e:  # a single bad ticker must not abort the scan
            log_ticker_failure(logger, ticker_clean, e)

    signals.sort(key=lambda x: x["confidence_score"], reverse=True)
    logger.info(
        "mean_reversion scan: %d/%d tickers with data, %d signals",
        len(all_data), len(jk_list), len(signals),
    )

    return build_envelope(
        strategy=STRATEGY,
        signals=signals,
        total_scanned=len(jk_list),
        downloaded=len(all_data),
        failed=len(jk_list) - len(all_data),
        filters={
            "rsi_threshold": rsi_thresh,
            "min_volume": min_vol,
            "min_below_sma20_pct": min_below_pct,
        },
        elapsed_s=elapsed_since(started),
        top_n=top_n,
    )


async def scan_mean_reversion(
    rsi_threshold: float = DEFAULT_RSI_THRESH,
    min_volume: int = DEFAULT_MIN_VOLUME,
    min_below_sma20_pct: float = DEFAULT_MIN_BELOW_SMA20_PCT,
) -> dict:
    """Run Mean Reversion deep-oversold scan across the BEI universe."""
    try:
        rsi_threshold = float(rsi_threshold)
        min_volume = int(min_volume)
        min_below_sma20_pct = float(min_below_sma20_pct)
    except (TypeError, ValueError):
        return {"error": True, "message": "Scan parameters must be numeric."}

    cache_key = f"mr_{rsi_threshold}_{min_volume}_{min_below_sma20_pct}"
    cached = cache.get(STRATEGY, cache_key)
    if cached is not None:
        return cached

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                _run_full_scan, rsi_threshold, min_volume, min_below_sma20_pct, 10
            ),
            timeout=_T_SCAN,
        )
    except asyncio.TimeoutError:
        return {
            "error": True,
            "error_type": "timeout",
            "message": f"Mean reversion scan exceeded {_T_SCAN:.0f}s.",
            "suggestion": "Retry — upstream price data was slow. Results cache for 4h once complete.",
        }
    except Exception as e:
        logger.exception("mean_reversion scan failed")
        return {"error": True, "error_type": "scan_failed", "message": str(e)}

    cache.set(STRATEGY, cache_key, result, _TTL_SCAN)
    return result
