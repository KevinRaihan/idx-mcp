"""Volume Accumulation Scanner.

Finds stocks that have traded 300%+ of their average 20-day volume today, 
but the price range remains tight (accumulation without price explosion yet).
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

logger = logging.getLogger("idx-mcp.tools.volume_accumulation")

DEFAULT_MIN_VOLUME = 1_000_000
DEFAULT_VOL_MULTIPLE = 3.0
BATCH_SIZE = 80
MIN_ROWS = 25
_TTL_SCAN = 14_400
_T_SCAN = 150.0

def _enrich_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["vol_20d_avg"] = df["Volume"].rolling(20).mean()
    # Price spread (high - low) as % of close
    df["price_spread_pct"] = (df["High"] - df["Low"]) / df["Close"] * 100
    return df

def _passes_entry_filters(row: pd.Series, df: pd.DataFrame, min_vol: int, vol_multiple: float) -> bool:
    close = _f(row.get("Close"))
    vol = _f(row.get("Volume"))
    vol_avg = _f(row.get("vol_20d_avg"))
    spread = _f(row.get("price_spread_pct"))
    
    if None in (close, vol, vol_avg, spread): return False
    if vol_avg <= 0: return False
    
    return (
        vol >= min_vol
        and (vol / vol_avg) >= vol_multiple
        and spread <= 5.0  # Tight range (max 5% intraday spread)
        and close >= df["Close"].iloc[-2] # Close is higher or equal to yesterday (not a dump)
    )

def _build_signal(ticker_clean: str, df: pd.DataFrame, min_vol: int, vol_multiple: float) -> dict | None:
    if df is None or len(df) < MIN_ROWS: return None
    row = df.iloc[-1]
    if not _passes_entry_filters(row, df, min_vol, vol_multiple): return None
    
    close = _f(row.get("Close"))
    vol_ratio = row.get("Volume") / row.get("vol_20d_avg")
    spread = _f(row.get("price_spread_pct"))
    
    score = 0.0
    score += 50 if vol_ratio > 5.0 else 30 if vol_ratio > 4.0 else 20
    score += 30 if spread < 2.0 else 20 if spread < 3.0 else 10
    score += 20 if close > df["Close"].iloc[-2] * 1.02 else 10 # Slight positive drift
    
    return {
        "ticker": ticker_clean,
        "close": close,
        "volume_ratio": safe_round(vol_ratio, 2),
        "intraday_spread_pct": safe_round(spread, 2),
        "confidence_score": min(score, 100.0)
    }

def _run_full_scan(min_vol: int, vol_multiple: float, top_n: int = 10) -> dict:
    tickers = _load_tickers()
    jk_list = [_to_jk(t) for t in tickers]
    all_data = {}
    for i in range(0, len(jk_list), BATCH_SIZE):
        all_data.update(_download_batch(jk_list[i:i+BATCH_SIZE], period="1mo"))
    
    signals = []
    for ticker_clean, df in all_data.items():
        try:
            enriched = _enrich_df(df)
            signal = _build_signal(ticker_clean, enriched, min_vol, vol_multiple)
            if signal: signals.append(signal)
        except Exception: continue
        
    signals.sort(key=lambda x: x["confidence_score"], reverse=True)
    return {"top_10": signals[:top_n]}

async def scan_volume_accumulation(min_volume: int = 1_000_000, vol_multiple: float = 3.0) -> dict:
    """Run Volume Accumulation scan."""
    cache_key = f"va_scan_{min_volume}_{vol_multiple}"
    if cached := cache.get("scan_volume_accumulation", cache_key): return cached
    try:
        result = await asyncio.wait_for(asyncio.to_thread(_run_full_scan, int(min_volume), float(vol_multiple), 10), timeout=_T_SCAN)
        cache.set("scan_volume_accumulation", cache_key, result, _TTL_SCAN)
        return result
    except Exception as e:
        return {"error": True, "message": str(e)}
