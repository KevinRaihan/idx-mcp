"""Mean Reversion (Deep Oversold) Scanner.

Finds stocks that have deviated significantly below their short-term moving average
(SMA 20) with deeply oversold RSI (< 30) and high relative volume, indicating capitulation.
"""
import asyncio
import logging
import math
import numpy as np
import pandas as pd

from ..utils.cache import cache
from ..utils.formatting import safe_round
from ..utils.ticker import validate_ticker
from ..utils.time_utils import format_wib_iso, now_wib

from .scanner import (
    _download_batch, _load_tickers, _strip_jk, _to_jk, _f, _i, get_tick_size
)

logger = logging.getLogger("idx-mcp.tools.mean_reversion")

DEFAULT_RSI_THRESH = 30.0
DEFAULT_MIN_VOLUME = 500_000
BATCH_SIZE = 80
MIN_ROWS = 50
_TTL_SCAN = 14_400
_T_SCAN = 150.0

def _compute_rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def _enrich_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["SMA20"] = df["Close"].rolling(20).mean()
    df["rsi"] = _compute_rsi(df["Close"])
    df["vol_20d_avg"] = df["Volume"].rolling(20).mean()
    return df

def _passes_entry_filters(row: pd.Series, rsi_thresh: float, min_vol: int) -> bool:
    close = _f(row.get("Close"))
    sma20 = _f(row.get("SMA20"))
    rsi = _f(row.get("rsi"))
    vol = _f(row.get("Volume"))
    
    if None in (close, sma20, rsi, vol): return False
    
    return (
        rsi < rsi_thresh
        and close < sma20 * 0.95  # At least 5% below SMA20
        and vol >= min_vol
    )

def _build_signal(ticker_clean: str, df: pd.DataFrame, rsi_thresh: float, min_vol: int) -> dict | None:
    if df is None or len(df) < MIN_ROWS: return None
    row = df.iloc[-1]
    if not _passes_entry_filters(row, rsi_thresh, min_vol): return None
    
    close = _f(row.get("Close"))
    sma20 = _f(row.get("SMA20"))
    rsi = _f(row.get("rsi"))
    dist_pct = (sma20 - close) / close * 100
    
    score = 0.0
    score += 40 if rsi < 20 else 25 if rsi < 25 else 15
    score += 40 if dist_pct > 15 else 25 if dist_pct > 10 else 15
    vol_ratio = row.get("Volume") / row.get("vol_20d_avg") if row.get("vol_20d_avg") else 0
    score += 20 if vol_ratio > 2.0 else 10 if vol_ratio > 1.0 else 5
    
    return {
        "ticker": ticker_clean,
        "close": close,
        "rsi": safe_round(rsi, 1),
        "sma20": safe_round(sma20, 0),
        "distance_below_sma20_pct": safe_round(dist_pct, 2),
        "volume_ratio": safe_round(vol_ratio, 2),
        "confidence_score": min(score, 100.0)
    }

def _run_full_scan(rsi_thresh: float, min_vol: int, top_n: int = 10) -> dict:
    tickers = _load_tickers()
    jk_list = [_to_jk(t) for t in tickers]
    all_data = {}
    for i in range(0, len(jk_list), BATCH_SIZE):
        all_data.update(_download_batch(jk_list[i:i+BATCH_SIZE], period="6mo"))
    
    signals = []
    for ticker_clean, df in all_data.items():
        try:
            enriched = _enrich_df(df)
            signal = _build_signal(ticker_clean, enriched, rsi_thresh, min_vol)
            if signal: signals.append(signal)
        except Exception: continue
        
    signals.sort(key=lambda x: x["confidence_score"], reverse=True)
    return {"top_10": signals[:top_n]}

async def scan_mean_reversion(rsi_threshold: float = 30.0, min_volume: int = 500_000) -> dict:
    """Run Mean Reversion deep oversold scan."""
    cache_key = f"mr_scan_{rsi_threshold}_{min_volume}"
    if cached := cache.get("scan_mean_reversion", cache_key): return cached
    try:
        result = await asyncio.wait_for(asyncio.to_thread(_run_full_scan, float(rsi_threshold), int(min_volume), 10), timeout=_T_SCAN)
        cache.set("scan_mean_reversion", cache_key, result, _TTL_SCAN)
        return result
    except Exception as e:
        return {"error": True, "message": str(e)}
