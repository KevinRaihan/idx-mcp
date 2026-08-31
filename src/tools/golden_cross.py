"""Golden Cross + Stochastic Oversold Scanner.

Finds BEI stocks in a confirmed long-term uptrend (SMA50 > SMA200, golden cross)
that have temporarily pulled back into oversold stochastic territory — a
'buy the dip in an uptrend' setup.

Strategy overview:
  - Golden cross confirmed:  SMA50 > SMA200 (long-term bullish structure)
  - Price above SMA200:      trend intact — SMA200 acts as stop anchor
  - Stochastic Slow %K < 25: temporarily oversold / pulled back
  - Stochastic %K >= %D OR fresh bullish K>D cross: momentum turning up
  - Volume >= 500K:           sufficient liquidity
  - RSI 14 < 50:              confirms pullback (not overbought)

Confidence scoring (0-100):
  Stochastic depth         25 pts  (deeper oversold = better dip)
  Golden cross freshness   20 pts  (more recent cross = more timely)
  Stochastic momentum      15 pts  (K above D and rising = turning up)
  RSI reading              15 pts  (30-45 ideal zone in uptrend)
  Volume vs 20-day avg     15 pts  (above-avg volume confirms interest)
  Distance above SMA200    10 pts  (close to SMA200 = better R/R)
"""

import asyncio
import logging
import math
import threading

import numpy as np
import pandas as pd

from ..utils.cache import cache
from ..utils.formatting import safe_round
from ..utils.ticker import validate_ticker
from ..utils.time_utils import format_wib_iso, now_wib

# Re-use shared scanner utilities (download, ticker helpers, NaN coercions)
from .scanner import (
    _download_batch,
    _load_tickers,
    _strip_jk,
    _to_jk,
    _f,
    _i,
    get_tick_size,
)
from .universe import load_universe

logger = logging.getLogger("idx-mcp.tools.golden_cross")

# ── Constants ─────────────────────────────────────────────────────────────────

DEFAULT_STOCH_THRESH = 25.0       # Stochastic %K oversold threshold
DEFAULT_MIN_VOLUME   = 500_000    # Lower than MA Ketat — allows smaller caps
FRESH_CROSS_DAYS     = 10         # "Fresh" golden cross window (trading sessions)
STOCH_FRESH_LOOKBACK = 3          # K>D crossover recency window (sessions)
BATCH_SIZE           = 80         # Tickers per yfinance download call
MIN_ROWS             = 210        # Minimum rows required (200 for SMA200 + buffer)

# Cache TTLs (seconds)
_TTL_SCAN    = 14_400   # 4 h — full scan is expensive
_TTL_ANALYZE =  3_600   # 1 h

# Hard timeouts (seconds)
_T_SCAN    = 150.0
_T_ANALYZE =  30.0

_DISCLAIMER = (
    "This output is for educational and analytical purposes only. "
    "Not financial advice. Trading decisions remain the user's full responsibility."
)


# ── Pure-pandas indicator helpers ─────────────────────────────────────────────

def _sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(window=n).mean()


def _ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False).mean()


def _compute_rsi(series: pd.Series, length: int = 14) -> pd.Series:
    """Wilder-smoothed RSI (identical implementation to scanner.py)."""
    delta    = series.diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _compute_stochastic_slow(
    df:       pd.DataFrame,
    k_period: int = 14,
    slowing:  int = 3,
    d_period: int = 3,
) -> tuple[pd.Series, pd.Series]:
    """Stochastic Slow %K and %D (pure pandas, no numba).

    Fast %K  = 100 * (Close - LowestLow_n) / (HighestHigh_n - LowestLow_n)
    Slow %K  = SMA(fast_k, slowing)   ← the "slowing" period smooths %K
    %D       = SMA(slow_k, d_period)  ← signal line
    """
    low_min  = df["Low"].rolling(k_period).min()
    high_max = df["High"].rolling(k_period).max()
    denom    = (high_max - low_min).replace(0, np.nan)
    fast_k   = 100 * (df["Close"] - low_min) / denom
    slow_k   = fast_k.rolling(slowing).mean()    # Slow %K
    slow_d   = slow_k.rolling(d_period).mean()   # %D signal line
    return slow_k, slow_d


def _enrich_df(df: pd.DataFrame) -> pd.DataFrame:
    """Add all golden-cross indicator columns to an OHLCV DataFrame."""
    df = df.copy()
    df["SMA50"]       = _sma(df["Close"], 50)
    df["SMA200"]      = _sma(df["Close"], 200)
    df["SMA20"]       = _sma(df["Close"], 20)
    df["rsi"]         = _compute_rsi(df["Close"])
    df["vol_20d_avg"] = df["Volume"].rolling(20).mean()

    df["stoch_k"], df["stoch_d"] = _compute_stochastic_slow(df)

    # Boolean event columns for crossover detection
    df["golden_cross_event"] = (
        (df["SMA50"] > df["SMA200"]) &
        (df["SMA50"].shift(1) <= df["SMA200"].shift(1))
    )
    df["stoch_cross_event"] = (
        (df["stoch_k"] > df["stoch_d"]) &
        (df["stoch_k"].shift(1) <= df["stoch_d"].shift(1))
    )
    return df


# ── Pattern detection helpers ─────────────────────────────────────────────────

def _days_since_golden_cross(df: pd.DataFrame) -> int | None:
    """Return sessions since the most recent SMA50>SMA200 crossover, or None."""
    events = df.index[df["golden_cross_event"]]
    if len(events) == 0:
        return None
    last_cross_pos = df.index.get_loc(events[-1])
    return len(df) - 1 - last_cross_pos


def _cross_age(df: pd.DataFrame, days_since: int | None, confirmed: bool) -> str:
    """Say *why* days_since_cross is null, which the value alone cannot.

    The crossover can only be seen where both SMAs exist, so on a 1y fetch the
    detectable window is roughly 52 sessions, not 252. A stock whose SMA50 has
    been above its SMA200 for a year is `confirmed` with no event in view, and
    reporting a bare null there is indistinguishable from a stock that never
    crossed at all -- opposite situations. DMAS on 2026-08-31 was the first
    case: confirmed true, days_since_cross null.
    """
    if days_since is not None:
        return "measured"
    return "older_than_lookback" if confirmed else "no_cross_in_lookback"


def _detectable_cross_sessions(df: pd.DataFrame) -> int:
    """Sessions in which a crossover could have been observed at all."""
    both = df["SMA50"].notna() & df["SMA200"].notna()
    return int(both.sum())


def _stoch_momentum_bullish(df: pd.DataFrame, stoch_thresh: float) -> bool:
    """%K >= %D at latest bar, OR K>D cross occurred within last few sessions
    while %D was still in oversold territory at that time (not a falling knife).
    """
    row = df.iloc[-1]
    k   = _f(row.get("stoch_k"))
    d   = _f(row.get("stoch_d"))
    if k is None or d is None:
        return False

    # Current K already at or above D — momentum turning up
    if k >= d:
        return True

    # Check for a fresh K>D crossover from oversold within the lookback window
    for i in range(-STOCH_FRESH_LOOKBACK, -1):
        try:
            if df["stoch_cross_event"].iloc[i]:
                d_at_cross = _f(df["stoch_d"].iloc[i])
                if d_at_cross is not None and d_at_cross < stoch_thresh:
                    return True   # fresh oversold crossover within window
        except IndexError:
            break

    return False


# ── Entry filter ──────────────────────────────────────────────────────────────

FUNNEL_STAGES = (
    "enough_history",
    "passed_volume_floor",
    "golden_cross_in_effect",
    "above_sma200",
    "stochastic_oversold",
    "stochastic_momentum_bullish",
    "rsi_below_50",
)


def _passes_entry_filters(
    row:          pd.Series,
    df:           pd.DataFrame,
    stoch_thresh: float = DEFAULT_STOCH_THRESH,
    min_vol:      int   = DEFAULT_MIN_VOLUME,
    funnel=None,
) -> bool:
    """All gate conditions for the Golden Cross + Stochastic Oversold signal.

    Evaluated one stage at a time rather than as a single boolean chain so the
    funnel can report which condition rejected the ticker.
    """
    close  = _f(row.get("Close"))
    sma50  = _f(row.get("SMA50"))
    sma200 = _f(row.get("SMA200"))
    stk    = _f(row.get("stoch_k"))
    _v     = _f(row.get("Volume"))
    volume = _v if _v is not None else 0.0
    rsi    = _f(row.get("rsi"))

    if None in (close, sma50, sma200, stk):
        return False

    if volume < min_vol:                                  # minimum liquidity
        return False
    if funnel:
        funnel.passed("passed_volume_floor")

    if sma50 <= sma200:                                   # golden cross in effect
        return False
    if funnel:
        funnel.passed("golden_cross_in_effect")

    if close <= sma200:                                   # above long-term trend
        return False
    if funnel:
        funnel.passed("above_sma200")

    if stk >= stoch_thresh:                               # oversold stochastic
        return False
    if funnel:
        funnel.passed("stochastic_oversold")

    if not _stoch_momentum_bullish(df, stoch_thresh):     # not a falling knife
        return False
    if funnel:
        funnel.passed("stochastic_momentum_bullish")

    if rsi is not None and rsi >= 50:                     # pullback confirmed
        return False
    if funnel:
        funnel.passed("rsi_below_50")

    return True


# ── Confidence score ──────────────────────────────────────────────────────────

def compute_gc_confidence_score(
    row:              pd.Series,
    df:               pd.DataFrame,
    days_since_cross: int | None,
) -> float:
    """0–100 confidence score for the golden cross + stochastic dip-buy signal.

    Components:
      Stochastic depth         25 pts  (lower %K = deeper dip = better)
      Golden cross freshness   20 pts  (more recent = more timely entry)
      Stochastic momentum      15 pts  (K above D and still rising)
      RSI reading              15 pts  (30-45 ideal dip zone in an uptrend)
      Volume vs 20-day avg     15 pts  (above-avg volume confirms participation)
      Distance above SMA200    10 pts  (closer = tighter stop, better R/R)
    """
    score = 0.0

    stk     = _f(row.get("stoch_k"))
    std     = _f(row.get("stoch_d"))
    rsi     = _f(row.get("rsi"))
    close   = _f(row.get("Close"))
    sma200  = _f(row.get("SMA200"))
    _v      = _f(row.get("Volume"))
    volume  = _v if _v is not None else 0.0
    _avg    = _f(row.get("vol_20d_avg"))
    vol_avg = _avg if _avg is not None else 1.0

    # 1. Stochastic depth: lower %K = deeper oversold = better dip
    _sk = stk if stk is not None else DEFAULT_STOCH_THRESH
    score += 25 if _sk < 10 else 20 if _sk < 15 else 15 if _sk < 20 else 10

    # 2. Golden cross freshness
    if days_since_cross is not None:
        score += 20 if days_since_cross <= 5 else 15 if days_since_cross <= 10 else 10 if days_since_cross <= 20 else 5
    else:
        score += 5   # long-standing cross — valid but less timely

    # 3. Stochastic momentum: K above D and still rising?
    if stk is not None and std is not None:
        k_prev = _f(df["stoch_k"].iloc[-2]) if len(df) >= 2 else None
        if stk >= std:
            score += 15 if (k_prev is not None and stk > k_prev) else 10
        # else K still below D → 0 pts (can still pass via fresh crossover filter)

    # 4. RSI: 30–45 is the ideal dip zone in an uptrend
    if rsi is not None:
        score += 15 if 30 <= rsi <= 45 else 10 if 25 <= rsi <= 55 else 3

    # 5. Volume vs 20-day average
    ratio = volume / vol_avg if vol_avg > 0 else 0.0
    score += 15 if ratio >= 2.0 else 12 if ratio >= 1.5 else 9 if ratio >= 1.0 else 5

    # 6. Distance above SMA200: closer = tighter stop-loss = better R/R
    if close and sma200 and sma200 > 0:
        dist_pct = (close - sma200) / sma200 * 100
        score += 10 if dist_pct <= 5 else 7 if dist_pct <= 10 else 3

    return min(round(score, 1), 100.0)


# ── Signal builder ────────────────────────────────────────────────────────────

def _build_gc_signal(
    ticker_clean: str,
    df:           pd.DataFrame,
    stoch_thresh: float = DEFAULT_STOCH_THRESH,
    min_vol:      int   = DEFAULT_MIN_VOLUME,
    funnel=None,
) -> dict | None:
    """Return a golden-cross signal dict if all entry filters pass, else None."""
    if df is None or len(df) < MIN_ROWS:
        return None
    if funnel:
        funnel.passed("enough_history")

    row   = df.iloc[-1]
    close = _f(row.get("Close"))
    if close is None:
        return None

    if not _passes_entry_filters(row, df, stoch_thresh, min_vol, funnel):
        return None

    sma50            = _f(row.get("SMA50"))
    sma200           = _f(row.get("SMA200"))
    stk              = _f(row.get("stoch_k"))
    std              = _f(row.get("stoch_d"))
    rsi              = _f(row.get("rsi"))
    days_since_cross = _days_since_golden_cross(df)
    tick             = get_tick_size(close)

    # Stop-loss anchored at SMA200 (the key trend support in this strategy)
    if sma200 and sma200 > 0:
        stop_loss = max(sma200 - tick, tick)   # one tick below SMA200, floor at 1 tick
    else:
        stop_loss = close * 0.93

    score = compute_gc_confidence_score(row, df, days_since_cross)

    return {
        "ticker":                  ticker_clean,
        "close":                   close,
        "golden_cross_confirmed":  True,
        "days_since_golden_cross": days_since_cross,
        "cross_age":               _cross_age(df, days_since_cross, True),
        "fresh_golden_cross":      (
            days_since_cross is not None and days_since_cross <= FRESH_CROSS_DAYS
        ),
        "sma50":      safe_round(sma50, 0),
        "sma200":     safe_round(sma200, 0),
        "stoch_k":    safe_round(stk, 1),
        "stoch_d":    safe_round(std, 1),
        "stoch_oversold": stk < stoch_thresh if stk is not None else False,
        "rsi":        safe_round(rsi, 1),
        "volume":     _i(row.get("Volume")),
        "distance_from_sma200_pct": safe_round(
            (close - sma200) / sma200 * 100, 2
        ) if sma200 else None,
        "entry_zone":    [
            safe_round(close - tick * 2, 0),
            safe_round(close + tick * 2, 0),
        ],
        "stop_loss":     safe_round(stop_loss, 0),
        "stop_loss_pct": safe_round(
            (stop_loss - close) / close * 100, 2
        ) if close else None,
        "confidence_score": score,
    }


# ── Rule-based prediction (no ML) ────────────────────────────────────────────

def _predict_gc(signal: dict, df: pd.DataFrame, horizon: int = 7) -> dict:
    """Short-term directional forecast for golden cross + stochastic dip-buy.

    Rule-based only — no ML, fully auditable and transparent.
    """
    close = signal["close"]
    score = signal["confidence_score"]

    # ATR (Wilder smoothed) — NaN-guarded
    prev_close = df["Close"].shift(1)
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - prev_close).abs(),
        (df["Low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr_raw = _f(tr.ewm(alpha=1 / 14, adjust=False).mean().iloc[-1])
    atr     = atr_raw if atr_raw is not None else close * 0.02   # fallback: 2%
    atr_pct = (atr / close) * 100 if close else 2.0

    # Direction composite — explicit None guards throughout
    _stk  = signal.get("stoch_k"); stk  = _stk  if _stk  is not None else DEFAULT_STOCH_THRESH
    _rsi  = signal.get("rsi");     rsi  = _rsi  if _rsi  is not None else 50.0
    fresh = signal.get("fresh_golden_cross", False)
    _dist = signal.get("distance_from_sma200_pct"); dist = _dist if _dist is not None else 10.0

    d = 0.0
    d += 0.30 * (1 - min(stk / DEFAULT_STOCH_THRESH, 1))   # deeper oversold = stronger
    d += 0.20 * (1.0 if fresh else 0.5)                    # fresh golden cross bonus
    d += 0.20 * (max(0, min((50 - rsi) / 30, 1)))          # RSI pullback headroom
    d += 0.15 * (1 - min(dist / 20.0, 1))                  # close to SMA200 = better R/R
    d += 0.15 * (score / 100)                               # overall signal quality

    if d >= 0.65:
        strength = "STRONG"
        lo_mult, hi_mult = 2.5, 4.0
    elif d >= 0.45:
        strength = "MEDIUM"
        lo_mult, hi_mult = 1.5, 2.5
    else:
        strength = "WEAK"
        lo_mult, hi_mult = 0.5, 1.5

    gain_lo   = safe_round(atr_pct * lo_mult, 1)
    gain_hi   = safe_round(atr_pct * hi_mult, 1)
    target_lo = safe_round(close * (1 + (gain_lo or 0) / 100), 0)
    target_hi = safe_round(close * (1 + (gain_hi or 0) / 100), 0)
    rr        = safe_round((gain_lo or 0) / abs(signal.get("stop_loss_pct") or 5), 2)

    # Rationale — [0].upper()+[1:] preserves acronym casing (MACD, RSI)
    parts = []
    if stk < 10:
        parts.append(f"deep stochastic oversold ({stk:.1f})")
    elif stk < DEFAULT_STOCH_THRESH:
        parts.append(f"stochastic oversold ({stk:.1f})")
    if fresh:
        parts.append(f"fresh golden cross (SMA50/SMA200 crossed {signal.get('days_since_golden_cross')} sessions ago)")
    if rsi < 40:
        parts.append(f"RSI deeply oversold ({rsi:.1f})")
    elif rsi < 50:
        parts.append(f"RSI in pullback zone ({rsi:.1f})")
    if dist is not None and dist <= 5:
        parts.append(f"price close to SMA200 support ({dist:.1f}% above)")
    joined = ", ".join(parts)
    rationale = (joined[0].upper() + joined[1:] + ".") if joined else "Golden cross dip-buy signal."

    return {
        "horizon_days":      horizon,
        "direction":         "BULLISH",
        "strength":          strength,
        "expected_gain_pct": [gain_lo, gain_hi],
        "target_price":      [target_lo, target_hi],
        "stop_loss_pct":     signal.get("stop_loss_pct"),
        "reward_risk_ratio": rr,
        "confidence_pct":    score,
        "rationale":         rationale,
    }


# ── Summary text builder ──────────────────────────────────────────────────────

def _build_summary(signals: list[dict], total_scanned: int) -> str:
    n = len(signals)
    if n == 0:
        return (
            f"Golden cross scan found no dip-buy signals out of {total_scanned} tickers. "
            "Most stocks may not currently be in a golden cross + oversold configuration."
        )
    top = signals[0]
    fresh_count = sum(1 for s in signals if s.get("fresh_golden_cross"))
    s = f"Golden cross scan found {n} dip-buy signal{'s' if n != 1 else ''} out of {total_scanned} tickers. "
    if fresh_count:
        s += f"{fresh_count} feature a fresh golden cross (within {FRESH_CROSS_DAYS} sessions). "
    s += (
        f"Top pick is {top['ticker']} — score {top['confidence_score']}/100, "
        f"stoch_k={top.get('stoch_k')}, RSI={top.get('rsi')}, "
        f"{'fresh cross, ' if top.get('fresh_golden_cross') else ''}"
        f"{top.get('distance_from_sma200_pct')}% above SMA200."
    )
    return s.strip()


# ── Full scan (sync, runs in asyncio.to_thread) ────────────────────────────────

def _run_full_scan(
    stoch_thresh: float = DEFAULT_STOCH_THRESH,
    min_vol:      int   = DEFAULT_MIN_VOLUME,
    top_n:        int   = 10,
) -> dict:
    """Synchronous full-market golden cross + stochastic scan."""
    total_attempted = len(_load_tickers())

    # One shared universe fetch backs every scanner; see tools/universe.py.
    all_data = load_universe(period="1y")

    from ._scan_common import Funnel
    funnel = Funnel(*FUNNEL_STAGES)

    total_scanned = len(all_data)
    signals: list[dict] = []

    for ticker_clean, df in all_data.items():
        try:
            enriched = _enrich_df(df)
            signal   = _build_gc_signal(ticker_clean, enriched, stoch_thresh, min_vol, funnel)
            if signal:
                signal["prediction"] = _predict_gc(signal, enriched)
                signals.append(signal)
        except Exception as e:
            logger.debug(f"Skipping {ticker_clean}: {e}")
            continue

    signals.sort(key=lambda x: x["confidence_score"], reverse=True)
    top = signals[:top_n]

    for i, s in enumerate(top):
        s["rank"] = i + 1

    return {
        "meta": {
            "tool":                    "Golden Cross + Stochastic Oversold Scanner",
            "version":                 "1.0.0",
            "scan_date":               now_wib().strftime("%Y-%m-%d"),
            "total_tickers_attempted": total_attempted,
            "total_tickers_scanned":   total_scanned,
            "total_signals_found":     len(signals),
            "parameters": {
                "stoch_threshold":  stoch_thresh,
                "min_volume":       min_vol,
                "fresh_cross_days": FRESH_CROSS_DAYS,
                "stoch_periods":    {"k": 14, "slowing": 3, "d": 3},
                "sma_periods":      {"golden_cross": [50, 200]},
            },
            "filter_funnel":           funnel.to_dict(),
        },
        "top_10":       top,
        "all_signals":  signals,
        "summary_text": _build_summary(top, total_scanned),
        "disclaimer":   _DISCLAIMER,
        "generated_at": format_wib_iso(),
    }


# ══════════════════════════════════════════════════════════════════════════════
# MCP Tool functions
# ══════════════════════════════════════════════════════════════════════════════

async def scan_golden_cross(
    stoch_threshold: float = DEFAULT_STOCH_THRESH,
    min_volume:      int   = DEFAULT_MIN_VOLUME,
) -> dict:
    """Run full BEI Golden Cross + Stochastic Oversold scan.

    Finds BEI stocks in a confirmed SMA50>SMA200 uptrend that have pulled back
    to oversold stochastic levels. Returns ranked dip-buy signals.

    Args:
        stoch_threshold: Stochastic %K oversold threshold (default 25.0)
        min_volume:      Minimum daily volume (default 500,000)
    """
    stoch_threshold = float(stoch_threshold) if stoch_threshold else DEFAULT_STOCH_THRESH
    _mv             = int(min_volume) if min_volume else DEFAULT_MIN_VOLUME

    cache_key = f"gc_scan_{stoch_threshold}_{_mv}"
    cached = cache.get("scan_golden_cross", cache_key)
    if cached is not None:
        return cached

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_run_full_scan, stoch_threshold, _mv, 10),
            timeout=_T_SCAN,
        )
    except asyncio.TimeoutError:
        return {
            "error": True,
            "error_type": "timeout",
            "message": f"Golden cross scan timed out after {_T_SCAN:.0f}s. Try again.",
            "partial_data": None,
            "suggestion": "The scan downloads data for ~180 tickers. Try again when network is stable.",
        }
    except Exception as e:
        logger.exception("Error in scan_golden_cross")
        return {
            "error": True,
            "error_type": "scan_failed",
            "message": f"Scan failed: {e}",
            "partial_data": None,
            "suggestion": "Try again later.",
        }

    cache.set("scan_golden_cross", cache_key, result, _TTL_SCAN)
    return result


async def get_top_golden_cross() -> dict:
    """Return the Top 10 Golden Cross dip-buy signals from today's scan.

    Uses cached scan if available; runs a fresh scan if not.
    Lightweight format optimised for LLM consumption.
    """
    full = await scan_golden_cross()
    if full.get("error"):
        return full

    top  = full.get("top_10", [])
    meta = full.get("meta", {})
    return {
        "scan_date":     meta.get("scan_date"),
        "total_scanned": meta.get("total_tickers_scanned"),
        "total_signals": meta.get("total_signals_found"),
        "top_10":        top,
        "summary_text":  full.get("summary_text"),
        "disclaimer":    _DISCLAIMER,
        "generated_at":  full.get("generated_at"),
    }


async def analyze_golden_cross(ticker: str, period: str = "1y") -> dict:
    """Deep Golden Cross + Stochastic analysis for a single BEI stock.

    Shows SMA50/SMA200 status, golden cross detection, stochastic readings,
    whether the stock passes all entry filters, and explains why if it fails.

    Args:
        ticker: IDX ticker symbol (e.g., 'BBCA')
        period: Lookback period — '1y' (default) or '2y'
    """
    try:
        normalized = validate_ticker(ticker)
    except ValueError as e:
        return {
            "error": True, "error_type": "invalid_ticker",
            "message": str(e), "partial_data": None,
            "suggestion": "Check the ticker symbol.",
        }

    period = (period or "1y").lower()
    if period not in ("1y", "2y"):
        period = "1y"

    cached = cache.get("gc_analyze", normalized, {"period": period})
    if cached is not None:
        return cached

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_analyze_single, normalized, period),
            timeout=_T_ANALYZE,
        )
    except asyncio.TimeoutError:
        return {
            "error": True, "error_type": "timeout",
            "message": f"Analysis for {normalized} timed out after {_T_ANALYZE:.0f}s.",
            "partial_data": None, "suggestion": "Try again.",
        }
    except Exception as e:
        logger.exception(f"Error in analyze_golden_cross for {normalized}")
        return {
            "error": True, "error_type": "data_unavailable",
            "message": f"Failed to analyze {normalized}: {e}",
            "partial_data": None, "suggestion": "Try again later.",
        }

    if not result.get("error"):
        cache.set("gc_analyze", normalized, result, _TTL_ANALYZE, {"period": period})
    return result


def _analyze_single(normalized: str, period: str) -> dict:
    """Sync worker: fetch + analyse one ticker for golden cross."""
    data = _download_batch([_to_jk(normalized)], period=period)
    df   = data.get(normalized)

    if df is None or df.empty:
        return {
            "error": True, "error_type": "data_unavailable",
            "message": f"No data for {normalized}.",
            "partial_data": None, "suggestion": "Verify ticker is active on BEI.",
        }

    if len(df) < MIN_ROWS:
        return {
            "error": True, "error_type": "data_unavailable",
            "message": (
                f"{normalized} has only {len(df)} trading rows — "
                f"need >= {MIN_ROWS} for a valid SMA200. "
                "Try period='2y' or verify the stock has sufficient history."
            ),
            "partial_data": None, "suggestion": "Try period='2y'.",
        }

    enriched         = _enrich_df(df)
    signal           = _build_gc_signal(normalized, enriched)
    row              = enriched.iloc[-1]
    close            = _f(row.get("Close"))
    sma50            = _f(row.get("SMA50"))
    sma200           = _f(row.get("SMA200"))
    stk              = _f(row.get("stoch_k"))
    std              = _f(row.get("stoch_d"))
    rsi              = _f(row.get("rsi"))
    days_since_cross = _days_since_golden_cross(enriched)

    base = {
        "ticker":    normalized,
        "close":     close,
        "scan_date": now_wib().strftime("%Y-%m-%d"),
        "golden_cross": {
            "confirmed":        sma50 is not None and sma200 is not None and sma50 > sma200,
            "sma50":            safe_round(sma50, 0),
            "sma200":           safe_round(sma200, 0),
            "days_since_cross": days_since_cross,
            "cross_age":        _cross_age(
                enriched, days_since_cross,
                sma50 is not None and sma200 is not None and sma50 > sma200,
            ),
            "detectable_cross_sessions": _detectable_cross_sessions(enriched),
            "fresh":            (
                days_since_cross is not None and days_since_cross <= FRESH_CROSS_DAYS
            ),
        },
        "stochastic": {
            "k":                safe_round(stk, 1),
            "d":                safe_round(std, 1),
            "oversold":         stk < DEFAULT_STOCH_THRESH if stk is not None else False,
            "momentum_bullish": _stoch_momentum_bullish(enriched, DEFAULT_STOCH_THRESH),
        },
        "rsi":         safe_round(rsi, 1),
        "volume":      _i(row.get("Volume")),
        "vol_20d_avg": safe_round(_f(row.get("vol_20d_avg")), 0),
        "distance_from_sma200_pct": safe_round(
            (close - sma200) / sma200 * 100, 2
        ) if close and sma200 else None,
        "passes_filters": signal is not None,
        "thresholds_used": {
            "stoch_threshold":  DEFAULT_STOCH_THRESH,
            "min_volume":       DEFAULT_MIN_VOLUME,
            "fresh_cross_days": FRESH_CROSS_DAYS,
        },
        "disclaimer": _DISCLAIMER,
    }

    if signal:
        base["signal"]     = signal
        base["prediction"] = _predict_gc(signal, enriched)
        base["assessment"] = "PASS — Golden cross dip-buy signal detected."
    else:
        reasons = []
        if sma50 is None or sma200 is None:
            reasons.append("insufficient data for SMA50/SMA200")
        elif sma50 <= sma200:
            reasons.append(
                f"no golden cross (SMA50={safe_round(sma50,0)} <= SMA200={safe_round(sma200,0)})"
            )
        if close and sma200 and close <= sma200:
            reasons.append(f"price below SMA200 (close={close}, SMA200={safe_round(sma200,0)})")
        if stk is not None and stk >= DEFAULT_STOCH_THRESH:
            reasons.append(f"stochastic not oversold (K={safe_round(stk,1)} >= {DEFAULT_STOCH_THRESH})")
        if not _stoch_momentum_bullish(enriched, DEFAULT_STOCH_THRESH):
            reasons.append("stochastic still declining (falling knife risk)")
        _v  = _f(row.get("Volume"))
        vol = _v if _v is not None else 0.0
        if vol < DEFAULT_MIN_VOLUME:
            reasons.append(f"volume={vol:,.0f} < {DEFAULT_MIN_VOLUME:,} (insufficient liquidity)")
        if rsi is not None and rsi >= 50:
            reasons.append(f"RSI={safe_round(rsi,1)} not below 50 (not a confirmed pullback)")
        base["assessment"] = (
            "FAIL — " + "; ".join(reasons) if reasons else "FAIL — signal conditions not met."
        )

    return base
