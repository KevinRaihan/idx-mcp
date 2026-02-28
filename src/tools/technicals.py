"""get_technicals tool — Technical indicators from historical price data."""

import asyncio
import logging

import pandas as pd
import pandas_ta as ta
import yfinance as yf

from ..utils.cache import TTLCache, cache
from ..utils.formatting import safe_round
from ..utils.ticker import to_yfinance_ticker, validate_ticker
from ..utils.time_utils import format_wib_iso

logger = logging.getLogger("idx-mcp.tools.technicals")


async def get_technicals(ticker: str, period: str = "3mo") -> dict:
    """Calculate technical indicators from historical price data.

    Args:
        ticker: IDX ticker symbol
        period: Lookback period — "3mo", "6mo", or "1y"

    Returns:
        Dict with technical indicator data or error response.
    """
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

    period = period.lower() if period else "3mo"
    if period not in ("3mo", "6mo", "1y"):
        period = "3mo"

    cached = cache.get("get_technicals", normalized, {"period": period})
    if cached is not None:
        return cached

    # Fetch enough history for 200-day SMA even if user requested shorter period
    fetch_period = "1y" if period in ("3mo", "6mo") else "2y"
    yf_ticker = to_yfinance_ticker(normalized)

    try:
        # Run both the yfinance fetch AND all pandas_ta computations inside a
        # single thread so the async event loop is never blocked by CPU-bound work.
        # This also allows multiple concurrent ticker requests to run truly in
        # parallel across the thread pool instead of serialising on the event loop.
        result = await asyncio.wait_for(
            asyncio.to_thread(_fetch_and_compute, yf_ticker, fetch_period, normalized, period),
            timeout=45.0,
        )
    except asyncio.TimeoutError:
        return {
            "error": True,
            "error_type": "timeout",
            "message": f"Data fetch/compute for {normalized} timed out after 45s. Yahoo Finance may be slow or rate-limiting.",
            "partial_data": None,
            "suggestion": "Try again in a few seconds.",
        }
    except Exception as e:
        logger.exception(f"Error calculating technicals for {normalized}")
        return {
            "error": True,
            "error_type": "data_unavailable",
            "message": f"Failed to calculate technicals for {normalized}: {str(e)}",
            "partial_data": None,
            "suggestion": "Try again later or check if the ticker has enough historical data.",
        }

    if result.get("error"):
        return result

    cache.set("get_technicals", normalized, result, TTLCache.TTL_TECHNICALS, {"period": period})
    return result


def _fetch_and_compute(yf_ticker: str, fetch_period: str, normalized: str, period: str) -> dict:
    """Synchronous helper: fetch history and compute all technical indicators.

    Runs entirely inside asyncio.to_thread so it never blocks the event loop.
    """
    stock = yf.Ticker(yf_ticker)
    hist = stock.history(period=fetch_period)

    if hist.empty or len(hist) < 20:
        return {
            "error": True,
            "error_type": "data_unavailable",
            "message": f"Insufficient historical data for {normalized} to calculate technicals.",
            "partial_data": None,
            "suggestion": "This ticker may be newly listed or have limited trading data.",
        }

    close = hist["Close"]
    high = hist["High"]
    low = hist["Low"]
    volume = hist["Volume"]
    current_price = float(close.iloc[-1])

    # Moving Averages
    ema_20 = ta.ema(close, length=20)
    sma_50 = ta.sma(close, length=50)
    sma_200 = ta.sma(close, length=200)

    ema_20_val = safe_round(float(ema_20.iloc[-1]), 2) if ema_20 is not None and not ema_20.empty else None
    sma_50_val = safe_round(float(sma_50.iloc[-1]), 2) if sma_50 is not None and not sma_50.empty and len(close) >= 50 else None
    sma_200_val = safe_round(float(sma_200.iloc[-1]), 2) if sma_200 is not None and not sma_200.empty and len(close) >= 200 else None

    price_vs_sma50 = safe_round(((current_price - sma_50_val) / sma_50_val) * 100, 2) if sma_50_val else None
    price_vs_sma200 = safe_round(((current_price - sma_200_val) / sma_200_val) * 100, 2) if sma_200_val else None

    # Golden/Death cross
    golden_cross = False
    death_cross = False
    if sma_50 is not None and sma_200 is not None and len(sma_50) >= 2 and len(sma_200) >= 2:
        try:
            sma50_curr = float(sma_50.iloc[-1])
            sma50_prev = float(sma_50.iloc[-2])
            sma200_curr = float(sma_200.iloc[-1])
            sma200_prev = float(sma_200.iloc[-2])
            if sma50_prev < sma200_prev and sma50_curr > sma200_curr:
                golden_cross = True
            if sma50_prev > sma200_prev and sma50_curr < sma200_curr:
                death_cross = True
        except (IndexError, ValueError):
            pass

    # RSI
    rsi = ta.rsi(close, length=14)
    rsi_val = safe_round(float(rsi.iloc[-1]), 2) if rsi is not None and not rsi.empty else None

    # MACD
    macd_df = ta.macd(close)
    macd_line = None
    signal_line = None
    histogram = None
    macd_signal = "neutral"
    if macd_df is not None and not macd_df.empty:
        cols = macd_df.columns
        # pandas_ta returns: MACD_{f}_{s}_{sig}, MACDh_{f}_{s}_{sig}, MACDs_{f}_{s}_{sig}
        # Match by prefix to avoid positional swaps
        macd_col   = next((c for c in cols if c.startswith("MACD_")), None)
        hist_col   = next((c for c in cols if c.startswith("MACDh_")), None)
        signal_col = next((c for c in cols if c.startswith("MACDs_")), None)
        if macd_col:
            macd_line = safe_round(float(macd_df[macd_col].iloc[-1]), 2)
        if signal_col:
            signal_line = safe_round(float(macd_df[signal_col].iloc[-1]), 2)
        if hist_col:
            histogram = safe_round(float(macd_df[hist_col].iloc[-1]), 2)
        if macd_line is not None and signal_line is not None:
            macd_signal = "bullish" if macd_line > signal_line else "bearish"

    # Stochastic
    stoch = ta.stoch(high, low, close)
    stoch_k = None
    stoch_d = None
    if stoch is not None and not stoch.empty:
        cols = stoch.columns
        stoch_k = safe_round(float(stoch[cols[0]].iloc[-1]), 2)
        stoch_d = safe_round(float(stoch[cols[1]].iloc[-1]), 2)

    # Volume analysis
    avg_vol_20 = safe_round(float(volume.tail(20).mean()), 0) if len(volume) >= 20 else None
    latest_vol = int(volume.iloc[-1])
    vol_ratio = safe_round(latest_vol / avg_vol_20, 2) if avg_vol_20 and avg_vol_20 > 0 else None

    if vol_ratio:
        vol_trend = "above_average" if vol_ratio > 1.2 else "below_average" if vol_ratio < 0.8 else "average"
    else:
        vol_trend = "unknown"

    # Support/Resistance (simple pivot point approach)
    recent = hist.tail(20)
    pivot = safe_round((float(recent["High"].max()) + float(recent["Low"].min()) + current_price) / 3, 2)

    # Support levels from recent lows
    lows = sorted(recent["Low"].tolist())
    supports = [safe_round(float(l), 0) for l in lows[:3]]

    # Resistance levels from recent highs
    highs = sorted(recent["High"].tolist(), reverse=True)
    resistances = [safe_round(float(h), 0) for h in highs[:3]]

    # ATR
    atr = ta.atr(high, low, close, length=14)
    atr_val = safe_round(float(atr.iloc[-1]), 2) if atr is not None and not atr.empty else None

    # Trend determination
    short_term = _determine_trend(current_price, ema_20_val, rsi_val, macd_signal)
    medium_term = _determine_trend(current_price, sma_50_val, rsi_val, macd_signal)
    long_term = _determine_trend(current_price, sma_200_val, rsi_val, macd_signal)

    # Overall signal
    signals = [short_term, medium_term, long_term]
    bullish_count = signals.count("bullish")
    bearish_count = signals.count("bearish")
    if bullish_count >= 2:
        overall = "buy"
    elif bearish_count >= 2:
        overall = "sell"
    else:
        overall = "hold"

    return {
        "ticker": normalized,
        "price": current_price,
        "moving_averages": {
            "ema_20": ema_20_val,
            "sma_50": sma_50_val,
            "sma_200": sma_200_val,
            "price_vs_sma50_pct": price_vs_sma50,
            "price_vs_sma200_pct": price_vs_sma200,
            "golden_cross": golden_cross,
            "death_cross": death_cross,
        },
        "momentum": {
            "rsi_14": rsi_val,
            "macd": {
                "macd_line": macd_line,
                "signal_line": signal_line,
                "histogram": histogram,
                "signal": macd_signal,
            },
            "stochastic": {
                "k": stoch_k,
                "d": stoch_d,
            },
        },
        "volume": {
            "avg_volume_20d": int(avg_vol_20) if avg_vol_20 else None,
            "latest_volume": latest_vol,
            "volume_ratio": vol_ratio,
            "volume_trend": vol_trend,
        },
        "levels": {
            "support": supports,
            "resistance": resistances,
            "pivot_point": pivot,
            "atr_14": atr_val,
        },
        "trend_summary": {
            "short_term": short_term,
            "medium_term": medium_term,
            "long_term": long_term,
            "overall_signal": overall,
        },
        "source": "yfinance + pandas-ta",
        "calculated_at": format_wib_iso(),
    }


def _determine_trend(price: float, ma_val: float | None, rsi: float | None, macd_signal: str) -> str:
    """Determine trend based on price vs MA, RSI, and MACD."""
    if ma_val is None:
        return "neutral"

    score = 0
    # Price above/below MA
    if price > ma_val:
        score += 1
    else:
        score -= 1

    # RSI
    if rsi is not None:
        if rsi > 60:
            score += 1
        elif rsi < 40:
            score -= 1

    # MACD
    if macd_signal == "bullish":
        score += 1
    elif macd_signal == "bearish":
        score -= 1

    if score >= 2:
        return "bullish"
    elif score <= -2:
        return "bearish"
    else:
        return "neutral"
