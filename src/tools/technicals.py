"""get_technicals tool — Technical indicators from historical price data.

All indicators are computed with pure pandas/numpy (no pandas-ta / numba).
This eliminates the multi-minute JIT-compilation delay that pandas-ta's numba
backend causes on the first call.
"""

import asyncio
import logging
import math

import numpy as np
import pandas as pd
import yfinance as yf

from ..utils.cache import TTLCache, cache
from ..utils.formatting import safe_round
from ..utils.ohlcv import drop_incomplete_bars
from ..utils.ticker import to_yfinance_ticker, validate_ticker
from ..utils.time_utils import format_wib_iso

logger = logging.getLogger("idx-mcp.tools.technicals")


# ---------------------------------------------------------------------------
# Pure-pandas indicator helpers (no numba, no pandas-ta)
# ---------------------------------------------------------------------------

def _ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def _sma(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(window=length).mean()


def _rsi(series: pd.Series, length: int = 14) -> pd.Series:
    """Wilder-smoothed RSI."""
    delta    = series.diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """Returns (macd_line, signal_line, histogram) as Series."""
    ema_fast    = _ema(series, fast)
    ema_slow    = _ema(series, slow)
    macd_line   = ema_fast - ema_slow
    signal_line = _ema(macd_line, signal)
    histogram   = macd_line - signal_line
    return macd_line, signal_line, histogram


def _stochastic(high: pd.Series, low: pd.Series, close: pd.Series,
                k_period: int = 14, d_period: int = 3):
    """Returns (%K, %D) as Series."""
    lowest_low   = low.rolling(window=k_period).min()
    highest_high = high.rolling(window=k_period).max()
    denom = (highest_high - lowest_low).replace(0, np.nan)
    k = 100 * (close - lowest_low) / denom
    d = k.rolling(window=d_period).mean()
    return k, d


def _atr(high: pd.Series, low: pd.Series, close: pd.Series,
         length: int = 14) -> pd.Series:
    """Average True Range using Wilder smoothing."""
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / length, adjust=False).mean()


def _safe_last(series: pd.Series, decimals: int = 2) -> float | None:
    """Return the last non-NaN value of a series, rounded."""
    if series is None or series.empty:
        return None
    try:
        val = float(series.iloc[-1])
        return None if math.isnan(val) or math.isinf(val) else safe_round(val, decimals)
    except (TypeError, ValueError):
        return None


def _safe_int(val) -> int:
    """Convert to int, returning 0 for NaN / None / non-numeric."""
    try:
        f = float(val)
        return 0 if math.isnan(f) or math.isinf(f) else int(f)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Main tool
# ---------------------------------------------------------------------------

async def get_technicals(ticker: str, period: str = "3mo") -> dict:
    """Calculate technical indicators from historical price data."""
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

    period = (period or "3mo").lower()
    if period not in ("3mo", "6mo", "1y"):
        period = "3mo"

    cached = cache.get("get_technicals", normalized, {"period": period})
    if cached is not None:
        return cached

    # Always fetch 1y so SMA-200 has enough data even for the 3mo view.
    fetch_period = "1y" if period in ("3mo", "6mo") else "2y"
    yf_ticker    = to_yfinance_ticker(normalized)

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_fetch_and_compute, yf_ticker, fetch_period, normalized, period),
            timeout=30.0,
        )
    except asyncio.TimeoutError:
        return {
            "error": True,
            "error_type": "timeout",
            "message": (
                f"Data fetch for {normalized} timed out after 30 s. "
                "Yahoo Finance may be slow or rate-limiting."
            ),
            "partial_data": None,
            "suggestion": "Try again in a few seconds.",
        }
    except Exception as e:
        logger.exception(f"Error calculating technicals for {normalized}")
        return {
            "error": True,
            "error_type": "data_unavailable",
            "message": f"Failed to calculate technicals for {normalized}: {e}",
            "partial_data": None,
            "suggestion": "Try again later or check if the ticker has enough historical data.",
        }

    if result.get("error"):
        return result

    cache.set("get_technicals", normalized, result, TTLCache.TTL_TECHNICALS, {"period": period})
    return result


# ---------------------------------------------------------------------------
# Sync worker (runs inside asyncio.to_thread)
# ---------------------------------------------------------------------------

def _fetch_and_compute(yf_ticker: str, fetch_period: str,
                       normalized: str, period: str) -> dict:
    """Fetch OHLCV history and compute all indicators synchronously.

    Pure pandas/numpy — no numba, no JIT, typically completes in < 1 s.
    """
    stock = yf.Ticker(yf_ticker)
    hist  = drop_incomplete_bars(stock.history(period=fetch_period))

    if hist.empty or len(hist) < 20:
        return {
            "error": True,
            "error_type": "data_unavailable",
            "message": f"Insufficient historical data for {normalized}.",
            "partial_data": None,
            "suggestion": "This ticker may be newly listed or have limited trading data.",
        }

    close  = hist["Close"].astype(float)
    high   = hist["High"].astype(float)
    low    = hist["Low"].astype(float)
    volume = hist["Volume"].astype(float)

    current_price = float(close.iloc[-1])

    # ── Moving Averages ──────────────────────────────────────────────────────
    ema20_s  = _ema(close, 20)
    sma50_s  = _sma(close, 50)
    sma200_s = _sma(close, 200)

    ema_20_val  = _safe_last(ema20_s)
    sma_50_val  = _safe_last(sma50_s)  if len(close) >= 50  else None
    sma_200_val = _safe_last(sma200_s) if len(close) >= 200 else None

    price_vs_sma50  = safe_round(((current_price - sma_50_val)  / sma_50_val)  * 100, 2) if sma_50_val  else None
    price_vs_sma200 = safe_round(((current_price - sma_200_val) / sma_200_val) * 100, 2) if sma_200_val else None

    # Golden / death cross (SMA50 crosses SMA200)
    golden_cross = death_cross = False
    if len(sma50_s.dropna()) >= 2 and len(sma200_s.dropna()) >= 2:
        try:
            s50_curr,  s50_prev  = float(sma50_s.iloc[-1]),  float(sma50_s.iloc[-2])
            s200_curr, s200_prev = float(sma200_s.iloc[-1]), float(sma200_s.iloc[-2])
            golden_cross = s50_prev < s200_prev and s50_curr > s200_curr
            death_cross  = s50_prev > s200_prev and s50_curr < s200_curr
        except (IndexError, ValueError):
            pass

    # ── Momentum ─────────────────────────────────────────────────────────────
    rsi_s   = _rsi(close, 14)
    rsi_val = _safe_last(rsi_s)

    macd_line_s, signal_line_s, hist_s = _macd(close)
    macd_line   = _safe_last(macd_line_s)
    signal_line = _safe_last(signal_line_s)
    histogram   = _safe_last(hist_s)
    macd_signal = (
        "bullish" if (macd_line is not None and signal_line is not None and macd_line > signal_line)
        else "bearish" if (macd_line is not None and signal_line is not None)
        else "neutral"
    )

    stoch_k_s, stoch_d_s = _stochastic(high, low, close)
    stoch_k = _safe_last(stoch_k_s)
    stoch_d = _safe_last(stoch_d_s)

    # ── Volume ───────────────────────────────────────────────────────────────
    avg_vol_20 = safe_round(float(volume.tail(20).mean()), 0) if len(volume) >= 20 else None
    # FIX: use _safe_int to guard against NaN volume rows (halted / suspended stocks)
    latest_vol = _safe_int(volume.iloc[-1])
    vol_ratio  = safe_round(latest_vol / avg_vol_20, 2) if avg_vol_20 and avg_vol_20 > 0 else None
    vol_trend  = (
        "above_average" if vol_ratio and vol_ratio > 1.2
        else "below_average" if vol_ratio and vol_ratio < 0.8
        else "average" if vol_ratio
        else "unknown"
    )

    # ── Support / Resistance (20-day pivot) ──────────────────────────────────
    recent      = hist.tail(20)
    pivot       = safe_round((float(recent["High"].max()) + float(recent["Low"].min()) + current_price) / 3, 2)
    supports    = [safe_round(float(v), 0) for v in sorted(recent["Low"].tolist())[:3]]
    resistances = [safe_round(float(v), 0) for v in sorted(recent["High"].tolist(), reverse=True)[:3]]

    # ── ATR ──────────────────────────────────────────────────────────────────
    atr_s   = _atr(high, low, close, 14)
    atr_val = _safe_last(atr_s)

    # ── Trend summary ────────────────────────────────────────────────────────
    short_term  = _determine_trend(current_price, ema_20_val,  rsi_val, macd_signal)
    medium_term = _determine_trend(current_price, sma_50_val,  rsi_val, macd_signal)
    long_term   = _determine_trend(current_price, sma_200_val, rsi_val, macd_signal)

    signals = [short_term, medium_term, long_term]
    overall = (
        "buy"  if signals.count("bullish") >= 2
        else "sell" if signals.count("bearish") >= 2
        else "hold"
    )

    return {
        "ticker": normalized,
        "price": current_price,
        "moving_averages": {
            "ema_20":              ema_20_val,
            "sma_50":              sma_50_val,
            "sma_200":             sma_200_val,
            "price_vs_sma50_pct":  price_vs_sma50,
            "price_vs_sma200_pct": price_vs_sma200,
            "golden_cross":        golden_cross,
            "death_cross":         death_cross,
        },
        "momentum": {
            "rsi_14": rsi_val,
            "macd": {
                "macd_line":   macd_line,
                "signal_line": signal_line,
                "histogram":   histogram,
                "signal":      macd_signal,
            },
            "stochastic": {"k": stoch_k, "d": stoch_d},
        },
        "volume": {
            "avg_volume_20d": int(avg_vol_20) if avg_vol_20 else None,
            "latest_volume":  latest_vol,
            "volume_ratio":   vol_ratio,
            "volume_trend":   vol_trend,
        },
        "levels": {
            "support":     supports,
            "resistance":  resistances,
            "pivot_point": pivot,
            "atr_14":      atr_val,
        },
        "trend_summary": {
            "short_term":     short_term,
            "medium_term":    medium_term,
            "long_term":      long_term,
            "overall_signal": overall,
        },
        "source":        "yfinance + pandas (pure numpy)",
        "calculated_at": format_wib_iso(),
    }


def _determine_trend(price: float, ma_val: float | None,
                     rsi: float | None, macd_signal: str) -> str:
    if ma_val is None:
        return "neutral"
    score  = 1 if price > ma_val else -1
    score += 1 if rsi is not None and rsi > 60 else -1 if rsi is not None and rsi < 40 else 0
    score += 1 if macd_signal == "bullish" else -1 if macd_signal == "bearish" else 0
    return "bullish" if score >= 2 else "bearish" if score <= -2 else "neutral"
