"""Volatility Squeeze Scanner.

Finds stocks where the Bollinger Bands are extremely tight (low volatility) 
combined with rising MACD momentum (predicting an imminent explosive breakout).
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

logger = logging.getLogger("idx-mcp.tools.vol_squeeze")

DEFAULT_MIN_VOLUME = 1_000_000
BATCH_SIZE = 80
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
    
    # 6-month min width
    df["bb_min_125d"] = df["bb_width"].rolling(125).min()
    
    # MACD
    ema_12 = _ema(df["Close"], 12)
    ema_26 = _ema(df["Close"], 26)
    df["macd_line"] = ema_12 - ema_26
    df["macd_signal"] = _ema(df["macd_line"], 9)
    df["macd_hist"] = df["macd_line"] - df["macd_signal"]
    
    df["vol_20d_avg"] = df["Volume"].rolling(20).mean()
    return df

def _passes_entry_filters(row: pd.Series, df: pd.DataFrame, min_vol: int) -> bool:
    close = _f(row.get("Close"))
    bb_width = _f(row.get("bb_width"))
    bb_min = _f(row.get("bb_min_125d"))
    vol = _f(row.get("Volume"))
    macd_hist = _f(row.get("macd_hist"))
    
    if None in (close, bb_width, bb_min, vol, macd_hist): return False
    
    macd_prev = _f(df.iloc[-2].get("macd_hist")) if len(df) > 1 else None
    
    return (
        bb_width <= bb_min * 1.10  # Within 10% of 6-month min bandwidth
        and macd_hist > (macd_prev if macd_prev is not None else 0) # Rising momentum
        and vol >= min_vol
    )

def _build_signal(ticker_clean: str, df: pd.DataFrame, min_vol: int) -> dict | None:
    if df is None or len(df) < MIN_ROWS: return None
    row = df.iloc[-1]
    if not _passes_entry_filters(row, df, min_vol): return None
    
    close = _f(row.get("Close"))
    bb_width = _f(row.get("bb_width"))
    macd_hist = _f(row.get("macd_hist"))
    vol_ratio = row.get("Volume") / row.get("vol_20d_avg") if row.get("vol_20d_avg") else 0
    
    score = 0.0
    score += 40 if bb_width < 0.05 else 25 if bb_width < 0.10 else 10
    score += 30 if macd_hist > 0 else 15
    score += 30 if vol_ratio > 1.5 else 15 if vol_ratio > 1.0 else 5
    
    return {
        "ticker": ticker_clean,
        "close": close,
        "bb_width": safe_round(bb_width, 4),
        "macd_hist": safe_round(macd_hist, 4),
        "volume_ratio": safe_round(vol_ratio, 2),
        "confidence_score": min(score, 100.0)
    }

def _run_full_scan(min_vol: int, top_n: int = 10) -> dict:
    tickers = _load_tickers()
    jk_list = [_to_jk(t) for t in tickers]
    all_data = {}
    for i in range(0, len(jk_list), BATCH_SIZE):
        all_data.update(_download_batch(jk_list[i:i+BATCH_SIZE], period="1y"))
    
    signals = []
    for ticker_clean, df in all_data.items():
        try:
            enriched = _enrich_df(df)
            signal = _build_signal(ticker_clean, enriched, min_vol)
            if signal: signals.append(signal)
        except Exception: continue
        
    signals.sort(key=lambda x: x["confidence_score"], reverse=True)
    return {"top_10": signals[:top_n]}

async def scan_volatility_squeeze(min_volume: int = 1_000_000) -> dict:
    """Run Volatility Squeeze scan."""
    cache_key = f"vs_scan_{min_volume}"
    if cached := cache.get("scan_vol_squeeze", cache_key): return cached
    try:
        result = await asyncio.wait_for(asyncio.to_thread(_run_full_scan, int(min_volume), 10), timeout=_T_SCAN)
        cache.set("scan_vol_squeeze", cache_key, result, _TTL_SCAN)
        return result
    except Exception as e:
        return {"error": True, "message": str(e)}
