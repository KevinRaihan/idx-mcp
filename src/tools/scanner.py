"""MA Ketat Scanner — BEI stock screener with tight moving average pattern detection.

Implements the MA Kuncup strategy: detects stocks where multiple SMA lines
converge (compress), signaling a potential high-probability breakout.

Based on SRS_MA_Ketat_Scanner.md v1.0.0
"""

import asyncio
import json
import logging
import math
import threading
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from ..utils.cache import cache
from ..utils.formatting import safe_round
from ..utils.ohlcv import drop_incomplete_bars
from ..utils.ticker import validate_ticker
from ..utils.time_utils import format_wib_iso, now_wib

logger = logging.getLogger("idx-mcp.tools.scanner")

# ── Paths & constants ─────────────────────────────────────────────────────────
DATA_DIR = Path(__file__).parent.parent / "data"

MA_PERIODS        = [3, 5, 10, 20, 50, 100]
DEFAULT_TICK_THRESH = 6.0
DEFAULT_VOL_THRESH  = 3.8
DEFAULT_MIN_VOLUME  = 1_000_000
BATCH_SIZE          = 80          # tickers per yfinance download call

# Cache TTLs
_TTL_SCAN     = 14_400   # 4 h  — full scan is expensive
_TTL_ANALYZE  =  3_600   # 1 h
_TTL_PREDICT  =  7_200   # 2 h
_TTL_BACKTEST = 86_400   # 24 h

# Hard timeouts
_T_SCAN      = 150.0
_T_ANALYZE   =  30.0
_T_BACKTEST  =  60.0

# Disclaimer (required by SRS §12)
_DISCLAIMER = (
    "This output is for educational and analytical purposes only. "
    "Not financial advice. Trading decisions remain the user's full responsibility."
)

# ── Ticker list ───────────────────────────────────────────────────────────────
_bei_tickers: list[str] | None = None
_tickers_lock = threading.Lock()


def _load_tickers() -> list[str]:
    """Load BEI tickers from JSON with thread-safe double-checked locking."""
    global _bei_tickers
    if _bei_tickers is not None:
        return _bei_tickers
    with _tickers_lock:
        if _bei_tickers is None:   # re-check after acquiring lock
            path = DATA_DIR / "bei_tickers.json"
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f).get("tickers", [])
            # Deduplicate while preserving order
            seen: set[str] = set()
            unique: list[str] = []
            for t in raw:
                t = t.strip().upper()
                if t and t not in seen:
                    seen.add(t)
                    unique.append(t)
            _bei_tickers = unique
    return _bei_tickers


def _to_jk(ticker: str) -> str:
    """Append .JK suffix if not already present."""
    return ticker if ticker.endswith(".JK") else f"{ticker}.JK"


def _strip_jk(ticker: str) -> str:
    return ticker[:-3] if ticker.endswith(".JK") else ticker


# ── BEI tick size ─────────────────────────────────────────────────────────────

# Canonical home is src/utils/tick.py, which also owns the level-snapping rules.
# Re-exported here so existing importers keep working.
from ..utils.tick import get_tick_size  # noqa: E402,F401


# ── Indicator helpers (pure pandas, no numba) ─────────────────────────────────

def _sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(window=n).mean()


def _ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False).mean()


def _compute_moving_averages(df: pd.DataFrame) -> pd.DataFrame:
    """Add MA_3, MA_5, MA_10, MA_20, MA_50, MA_100 columns in-place."""
    for p in MA_PERIODS:
        df[f"MA_{p}"] = _sma(df["Close"], p)
    return df


def _compute_range_ticks(df: pd.DataFrame) -> pd.Series:
    """Tick-adjusted spread: max(MA3..MA50, Close) − min(...) / tick_size(Close)."""
    cols = [f"MA_{p}" for p in [3, 5, 10, 20, 50]] + ["Close"]
    hi   = df[cols].max(axis=1)
    lo   = df[cols].min(axis=1)
    tick = df["Close"].apply(get_tick_size)
    return (hi - lo) / tick.replace(0, np.nan)


def _compute_vol_pct(df: pd.DataFrame, window: int = 10) -> pd.Series:
    """Rolling 10-day std of daily returns as a percentage."""
    return df["Close"].pct_change().rolling(window=window).std() * 100


def _compute_rsi(series: pd.Series, length: int = 14) -> pd.Series:
    """Wilder-smoothed RSI (same implementation as technicals.py)."""
    delta    = series.diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _compute_macd(series: pd.Series, fast: int = 12, slow: int = 26, sig: int = 9):
    """Returns (macd_line, signal_line, histogram) as Series."""
    ema_f  = _ema(series, fast)
    ema_s  = _ema(series, slow)
    line   = ema_f - ema_s
    signal = _ema(line, sig)
    hist   = line - signal
    return line, signal, hist


def _compute_bb_squeeze(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """Bollinger Band squeeze: bandwidth at or near a 6-month minimum."""
    sma  = df["Close"].rolling(window).mean()
    std  = df["Close"].rolling(window).std()
    bw   = (std * 2) / sma.replace(0, np.nan)
    # Within 10% of the rolling 125-day minimum bandwidth
    return bw <= bw.rolling(125).min() * 1.10


# ── NaN-safe coercions ────────────────────────────────────────────────────────

def _f(val) -> float | None:
    """Coerce to float, returning None for None / NaN / Inf."""
    if val is None:
        return None
    try:
        v = float(val)
        return None if math.isnan(v) or math.isinf(v) else v
    except (TypeError, ValueError):
        return None


def _i(val) -> int:
    """Coerce to int, returning 0 for None / NaN / Inf."""
    v = _f(val)
    return 0 if v is None else int(v)


# ── Signal filters ────────────────────────────────────────────────────────────

def _is_ma_tight(row: pd.Series, tick_thresh: float, vol_thresh: float) -> bool:
    rt = _f(row.get("range_ticks"))
    vp = _f(row.get("vol_pct"))
    return rt is not None and vp is not None and rt < tick_thresh and vp < vol_thresh


def _is_above_all_ma(row: pd.Series) -> bool:
    close = _f(row.get("Close"))
    if close is None:
        return False
    for p in [3, 5, 10, 20, 50]:
        ma = _f(row.get(f"MA_{p}"))
        if ma is None or close <= ma:
            return False
    return True


FUNNEL_STAGES = (
    "enough_history",
    "passed_volume_floor",
    "moving_averages_tight",
    "above_all_moving_averages",
    "above_ma100",
)


def _passes_entry_filters(row: pd.Series,
                           tick_thresh: float = DEFAULT_TICK_THRESH,
                           vol_thresh:  float = DEFAULT_VOL_THRESH,
                           min_vol:     int   = DEFAULT_MIN_VOLUME,
                           funnel=None) -> bool:
    """All gate conditions (SRS §5.1).

    Evaluated one stage at a time rather than as a single boolean chain so the
    funnel can report which condition rejected the ticker.
    """
    close   = _f(row.get("Close"))
    ma100   = _f(row.get("MA_100"))
    _vol    = _f(row.get("Volume"))
    volume  = _vol if _vol is not None else 0.0

    if close is None or ma100 is None:
        return False

    if volume < min_vol:
        return False
    if funnel:
        funnel.passed("passed_volume_floor")

    if not _is_ma_tight(row, tick_thresh, vol_thresh):
        return False
    if funnel:
        funnel.passed("moving_averages_tight")

    if not _is_above_all_ma(row):
        return False
    if funnel:
        funnel.passed("above_all_moving_averages")

    if close < ma100:
        return False
    if funnel:
        funnel.passed("above_ma100")

    return True


# ── Confidence score (SRS §5.3) ───────────────────────────────────────────────

def compute_confidence_score(row: pd.Series) -> float:
    """0–100 confidence score based on tick tightness, vol, volume, RSI, MACD, BB."""
    score = 0.0

    # Tick tightness 30 pts — explicit None guard so 0.0 is not treated as missing
    _rt = _f(row.get("range_ticks"))
    rt  = _rt if _rt is not None else 999.0
    score += 30 if rt <= 2 else 25 if rt <= 3 else 20 if rt <= 4 else 15 if rt <= 5 else 10

    # Volatility 20 pts
    _vp = _f(row.get("vol_pct"))
    vp  = _vp if _vp is not None else 999.0
    score += 20 if vp <= 1.0 else 17 if vp <= 1.5 else 14 if vp <= 2.0 else 11 if vp <= 2.5 else 8 if vp <= 3.0 else 5

    # Volume strength 15 pts
    _vol = _f(row.get("Volume"))
    vol  = _vol if _vol is not None else 0.0
    score += 15 if vol >= 10_000_000 else 12 if vol >= 5_000_000 else 9 if vol >= 3_000_000 else 6

    # RSI 15 pts
    rsi = _f(row.get("rsi"))
    if rsi is not None:
        score += 15 if 45 <= rsi <= 65 else 8 if 40 <= rsi <= 70 else 2

    # MACD 10 pts — reward fresh crossover most
    mh      = _f(row.get("macd_hist"))
    mh_prev = _f(row.get("macd_hist_prev"))
    if mh is not None and mh_prev is not None:
        if mh > 0 and mh_prev <= 0:
            score += 10   # fresh bullish crossover
        elif mh > 0:
            score += 6    # above signal line
        else:
            score += 1

    # BB squeeze 10 pts
    score += 10 if row.get("bb_squeeze") else 3

    return min(round(score, 1), 100.0)


# ── Per-ticker computation ────────────────────────────────────────────────────

def _enrich_df(df: pd.DataFrame) -> pd.DataFrame:
    """Add all indicator columns to an OHLCV DataFrame."""
    df = df.copy()
    df = _compute_moving_averages(df)
    df["range_ticks"]   = _compute_range_ticks(df)
    df["vol_pct"]       = _compute_vol_pct(df)
    df["rsi"]           = _compute_rsi(df["Close"])
    ml, ms, mh          = _compute_macd(df["Close"])
    df["macd_line"]     = ml
    df["macd_signal"]   = ms
    df["macd_hist"]     = mh
    df["macd_hist_prev"] = mh.shift(1)
    df["bb_squeeze"]    = _compute_bb_squeeze(df)
    return df


def _build_signal(ticker_clean: str, df: pd.DataFrame,
                  tick_thresh: float = DEFAULT_TICK_THRESH,
                  vol_thresh:  float = DEFAULT_VOL_THRESH,
                  funnel=None) -> dict | None:
    """Analyse the latest row of an enriched DataFrame.

    Returns a signal dict if all entry filters pass, else None.
    """
    if df is None or len(df) < 110:
        return None   # need 100 rows for MA_100 plus warm-up
    if funnel:
        funnel.passed("enough_history")

    row   = df.iloc[-1]
    close = _f(row.get("Close"))
    if close is None:
        return None

    if not _passes_entry_filters(row, tick_thresh, vol_thresh, funnel=funnel):
        return None

    tick    = get_tick_size(close)
    ma20    = _f(row.get("MA_20"))
    # Guard: only use ma20 as stop anchor when it is a valid positive price
    if ma20 is not None and ma20 > 0:
        stop_loss = max(ma20 - tick * 2, tick)   # floor at one tick
    else:
        stop_loss = close * 0.95

    macd_cross = False
    mh      = _f(row.get("macd_hist"))
    mh_prev = _f(row.get("macd_hist_prev"))
    if mh is not None and mh_prev is not None:
        macd_cross = mh > 0 and mh_prev <= 0

    score = compute_confidence_score(row)

    return {
        "ticker":        ticker_clean,
        "close":         close,
        "range_ticks":   safe_round(_f(row.get("range_ticks")), 2),
        "vol_pct":       safe_round(_f(row.get("vol_pct")), 2),
        "volume":        _i(row.get("Volume")),
        "rsi":           safe_round(_f(row.get("rsi")), 1),
        "macd_crossover": macd_cross,
        "macd_line":     safe_round(_f(row.get("macd_line")), 2),
        "macd_signal_val": safe_round(_f(row.get("macd_signal")), 2),
        "bb_squeeze":    bool(row.get("bb_squeeze")),
        "above_all_ma":  True,
        "ma_values": {
            "MA_3":    safe_round(_f(row.get("MA_3")),   0),
            "MA_5":    safe_round(_f(row.get("MA_5")),   0),
            "MA_10":   safe_round(_f(row.get("MA_10")),  0),
            "MA_20":   safe_round(_f(row.get("MA_20")),  0),
            "MA_50":   safe_round(_f(row.get("MA_50")),  0),
            "MA_100":  safe_round(_f(row.get("MA_100")), 0),
        },
        "entry_zone":      [safe_round(close - tick * 2, 0), safe_round(close + tick * 2, 0)],
        "stop_loss":       safe_round(stop_loss, 0),
        "stop_loss_pct":   safe_round(((stop_loss - close) / close) * 100, 2) if close else None,
        "confidence_score": score,
    }


# ── Prediction (SRS §8) ───────────────────────────────────────────────────────

def _predict(signal: dict, df: pd.DataFrame, horizon: int = 7) -> dict:
    """Rule-based short-term directional forecast (SRS §8.1).

    No ML — fully transparent and auditable.
    """
    close = signal["close"]
    score = signal["confidence_score"]

    # ATR-based range estimate — guard against NaN propagation (M-4)
    prev_close = df["Close"].shift(1)
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - prev_close).abs(),
        (df["Low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr_raw = _f(tr.ewm(alpha=1 / 14, adjust=False).mean().iloc[-1])
    atr = atr_raw if atr_raw is not None else close * 0.02   # fallback: 2% of price
    atr_pct = (atr / close) * 100 if close else 2.0

    # Direction factors — explicit None guards so 0.0 is not treated as missing (C-4)
    _rt  = signal.get("range_ticks"); rt  = _rt  if _rt  is not None else 6.0
    _vp  = signal.get("vol_pct");     vp  = _vp  if _vp  is not None else 3.8
    _rsi = signal.get("rsi");         rsi = _rsi if _rsi is not None else 50.0
    macd = signal.get("macd_crossover", False)

    d = 0.0
    d += 0.25 * (1 - min(rt / 6.0, 1))          # tick compression (lower = more)
    d += 0.20 * (1 - min(vp / 3.8, 1))           # vol compression
    d += 0.20 * (1.0 if macd else 0.3)            # MACD crossover
    d += 0.15 * (max(0, min((rsi - 30) / 40, 1))) # RSI in healthy range
    d += 0.20 * (score / 100)                     # overall score weight

    if d >= 0.65:
        strength = "STRONG"
        lo_mult, hi_mult = 2.5, 4.5
    elif d >= 0.45:
        strength = "MEDIUM"
        lo_mult, hi_mult = 1.5, 3.0
    else:
        strength = "WEAK"
        lo_mult, hi_mult = 0.5, 2.0

    gain_lo  = safe_round(atr_pct * lo_mult, 1)
    gain_hi  = safe_round(atr_pct * hi_mult, 1)
    target_lo = safe_round(close * (1 + gain_lo / 100), 0)
    target_hi = safe_round(close * (1 + gain_hi / 100), 0)
    rr = safe_round(gain_lo / abs(signal.get("stop_loss_pct") or 5), 2)

    rationale_parts = []
    if rt <= 3:
        rationale_parts.append(f"ultra-tight MA bundle ({rt} ticks)")
    elif rt <= 5:
        rationale_parts.append(f"tight MA bundle ({rt} ticks)")
    if macd:
        rationale_parts.append("MACD fresh bullish crossover")
    if signal.get("bb_squeeze"):
        rationale_parts.append("Bollinger Band squeeze active")
    if 45 <= (rsi or 0) <= 65:
        rationale_parts.append(f"RSI in healthy zone ({rsi})")
    # Use [0].upper() + [1:] instead of .capitalize() to preserve acronyms (L-5)
    joined = ", ".join(rationale_parts)
    rationale = (joined[0].upper() + joined[1:] + ".") if joined else "MA Ketat signal detected."

    return {
        "horizon_days":       horizon,
        "direction":          "BULLISH",
        "strength":           strength,
        "expected_gain_pct":  [gain_lo, gain_hi],
        "target_price":       [target_lo, target_hi],
        "stop_loss_pct":      signal.get("stop_loss_pct"),
        "reward_risk_ratio":  rr,
        "confidence_pct":     score,
        "rationale":          rationale,
    }


# ── Summary text builder ──────────────────────────────────────────────────────

def _build_summary(signals: list[dict], total_scanned: int) -> str:
    n = len(signals)
    if n == 0:
        return (
            f"Today's scan found no MA Ketat signals out of {total_scanned} tickers. "
            "Market may be in a trend phase with few consolidating stocks."
        )
    top = signals[0]
    s = (
        f"Today's scan found {n} MA Ketat signal{'s' if n != 1 else ''} "
        f"out of {total_scanned} tickers. "
        f"Top pick is {top['ticker']} with a {top['confidence_score']}/100 confidence score "
        f"(range_ticks={top['range_ticks']}, vol={top['vol_pct']}%, RSI={top['rsi']}). "
    )
    if top.get("macd_crossover"):
        s += "MACD crossover confirmed on top pick. "
    if top.get("bb_squeeze"):
        s += "Bollinger Band squeeze active — breakout imminent. "
    return s.strip()


# ── Batch OHLCV download (sync, runs in thread) ───────────────────────────────

def _download_batch(
    jk_tickers: list[str], period: str = "1y", min_rows: int = 20
) -> dict[str, pd.DataFrame]:
    """Batch-download OHLCV for a list of .JK tickers.

    yfinance 1.x always returns (Ticker, Field) MultiIndex when tickers is a list
    with group_by='ticker'. Each sub-DataFrame accessed via raw[ticker] has flat
    columns ['Open', 'High', 'Low', 'Close', 'Volume'].

    Returns dict mapping clean ticker → DataFrame.
    """
    results: dict[str, pd.DataFrame] = {}
    if not jk_tickers:
        return results

    try:
        raw = yf.download(
            tickers=jk_tickers,   # always pass as list for consistent MultiIndex output
            period=period,
            group_by="ticker",
            auto_adjust=True,
            threads=True,
            progress=False,
        )
    except Exception as e:
        logger.warning(f"Batch download failed: {e}")
        return results

    if raw.empty:
        return results

    # yfinance 1.x with group_by='ticker' and a list always returns MultiIndex
    if isinstance(raw.columns, pd.MultiIndex):
        for jk in jk_tickers:
            try:
                df = drop_incomplete_bars(raw[jk]).copy()
                if not df.empty and len(df) >= min_rows and "Close" in df.columns:
                    results[_strip_jk(jk)] = df
            except Exception as e:
                # Delisted / suspended tickers are absent from the frame; expected.
                logger.debug("no usable data for %s: %s", jk, e)
                continue
    else:
        # Fallback: flat columns (older yfinance or edge case)
        ticker_clean = _strip_jk(jk_tickers[0])
        df = drop_incomplete_bars(raw).copy()
        if not df.empty and len(df) >= min_rows and "Close" in df.columns:
            results[ticker_clean] = df

    return results


def _run_full_scan(
    tick_thresh: float = DEFAULT_TICK_THRESH,
    vol_thresh:  float = DEFAULT_VOL_THRESH,
    top_n:       int   = 10,
) -> dict:
    """Synchronous full-market scan. Runs in asyncio.to_thread."""
    # Imported here rather than at module scope: universe.py builds on
    # _download_batch from this module, so a top-level import would be circular.
    from .universe import load_universe

    total_attempted = len(_load_tickers())   # M-7: track attempted vs downloaded

    # One shared universe fetch backs every scanner; see tools/universe.py.
    all_data = load_universe(period="1y")

    from ._scan_common import Funnel
    funnel = Funnel(*FUNNEL_STAGES)

    total_scanned = len(all_data)
    signals: list[dict] = []

    for ticker_clean, df in all_data.items():
        try:
            enriched = _enrich_df(df)
            signal   = _build_signal(ticker_clean, enriched, tick_thresh, vol_thresh, funnel)
            if signal:
                pred = _predict(signal, enriched)
                signal["prediction"] = pred
                signals.append(signal)
        except Exception as e:
            logger.debug(f"Skipping {ticker_clean}: {e}")
            continue

    signals.sort(key=lambda x: x["confidence_score"], reverse=True)
    top10 = signals[:top_n]   # top_n is always passed explicitly from scan_today (C-5)

    for i, s in enumerate(top10):
        s["rank"] = i + 1

    return {
        "meta": {
            "tool":                   "MA Ketat Scanner",
            "version":                "1.0.0",
            "scan_date":              now_wib().strftime("%Y-%m-%d"),
            "total_tickers_attempted": total_attempted,   # M-7: attempted count
            "total_tickers_scanned":  total_scanned,
            "total_signals_found":    len(signals),
            "parameters": {
                "tick_threshold": tick_thresh,
                "vol_threshold":  vol_thresh,
                "min_volume":     DEFAULT_MIN_VOLUME,
                "ma_periods":     MA_PERIODS,
            },
            "filter_funnel":          funnel.to_dict(),
        },
        "top_10":       top10,
        "all_signals":  signals,
        "summary_text": _build_summary(top10, total_scanned),
        "disclaimer":   _DISCLAIMER,
        "generated_at": format_wib_iso(),
    }


# ══════════════════════════════════════════════════════════════════════════════
# MCP Tool functions
# ══════════════════════════════════════════════════════════════════════════════

async def scan_ma_breakout(tick_threshold: float = DEFAULT_TICK_THRESH,
                     vol_threshold:  float = DEFAULT_VOL_THRESH) -> dict:
    """Run full BEI MA Ketat scan for today.

    Scans all tickers in the curated BEI list, applies MA Ketat filters,
    scores each signal, and returns ranked results.

    Args:
        tick_threshold: MA range_ticks threshold (default 6.0, lower = stricter)
        vol_threshold:  Rolling 10-day volatility threshold in % (default 3.8)
    """
    tick_threshold = float(tick_threshold) if tick_threshold else DEFAULT_TICK_THRESH
    vol_threshold  = float(vol_threshold)  if vol_threshold  else DEFAULT_VOL_THRESH

    cache_key = f"scan_{tick_threshold}_{vol_threshold}"
    cached = cache.get("scan_ma_breakout", cache_key)
    if cached is not None:
        return cached

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_run_full_scan, tick_threshold, vol_threshold, 10),  # C-5: explicit top_n
            timeout=_T_SCAN,
        )
    except asyncio.TimeoutError:
        return {
            "error": True,
            "error_type": "timeout",
            "message": f"Full BEI scan timed out after {_T_SCAN:.0f}s. Try again or narrow the scan.",
            "partial_data": None,
            "suggestion": "The market scan downloads data for ~250 tickers. Try again when network is stable.",
        }
    except Exception as e:
        logger.exception("Error in scan_today")
        return {
            "error": True,
            "error_type": "scan_failed",
            "message": f"Scan failed: {e}",
            "partial_data": None,
            "suggestion": "Try again later.",
        }

    cache.set("scan_ma_breakout", cache_key, result, _TTL_SCAN)
    return result


async def get_top10() -> dict:
    """Return the Top 10 MA Ketat results from today's scan.

    Uses cached scan_ma_breakout if available; runs a fresh scan if not.
    Lightweight format optimised for LLM consumption.
    """
    full = await scan_ma_breakout()
    if full.get("error"):
        return full

    top = full.get("top_10", [])
    return {
        "scan_date":      full["meta"]["scan_date"],
        "total_scanned":  full["meta"]["total_tickers_scanned"],
        "total_signals":  full["meta"]["total_signals_found"],
        "top_10":         top,
        "market_context": _sector_context(top),
        "summary_text":   full.get("summary_text"),
        "disclaimer":     _DISCLAIMER,
        "generated_at":   full.get("generated_at"),
    }


async def analyze_ticker(ticker: str, period: str = "6mo") -> dict:
    """Deep MA Ketat analysis for a single stock.

    Args:
        ticker: IDX ticker symbol (e.g., "BBCA")
        period: Lookback period — "3mo", "6mo", "1y" (default "6mo")
    """
    try:
        normalized = validate_ticker(ticker)
    except ValueError as e:
        return {"error": True, "error_type": "invalid_ticker", "message": str(e),
                "partial_data": None, "suggestion": "Check the ticker symbol."}

    period = (period or "6mo").lower()
    if period not in ("3mo", "6mo", "1y"):
        period = "6mo"

    cached = cache.get("ma_analyze", normalized, {"period": period})
    if cached is not None:
        return cached

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_analyze_single, normalized, period),
            timeout=_T_ANALYZE,
        )
    except asyncio.TimeoutError:
        return {"error": True, "error_type": "timeout",
                "message": f"Analysis for {normalized} timed out after {_T_ANALYZE:.0f}s.",
                "partial_data": None, "suggestion": "Try again."}
    except Exception as e:
        logger.exception(f"Error analyzing {normalized}")
        return {"error": True, "error_type": "data_unavailable",
                "message": f"Failed to analyze {normalized}: {e}",
                "partial_data": None, "suggestion": "Try again later."}

    if not result.get("error"):
        cache.set("ma_analyze", normalized, result, _TTL_ANALYZE, {"period": period})
    return result


def _analyze_single(normalized: str, period: str) -> dict:
    """Sync worker: fetch + analyse one ticker."""
    yf_ticker = _to_jk(normalized)
    # C-1 fix: use the caller-supplied period (was hardcoded to "1y")
    data = _download_batch([yf_ticker], period=period)
    df   = data.get(normalized)

    if df is None or df.empty:
        return {"error": True, "error_type": "data_unavailable",
                "message": f"No data for {normalized}.",
                "partial_data": None, "suggestion": "Verify ticker is active on BEI."}

    enriched = _enrich_df(df)
    signal   = _build_signal(normalized, enriched)

    row   = enriched.iloc[-1]
    close = _f(row.get("Close"))

    base = {
        "ticker":      normalized,
        "close":       close,
        "scan_date":   now_wib().strftime("%Y-%m-%d"),
        "ma_tight":    signal is not None,
        "above_all_ma": _is_above_all_ma(row),
        "passes_filters": signal is not None,
        "indicators": {
            "range_ticks": safe_round(_f(row.get("range_ticks")), 2),
            "vol_pct_10d": safe_round(_f(row.get("vol_pct")), 2),
            "rsi_14":      safe_round(_f(row.get("rsi")), 1),
            "macd_line":   safe_round(_f(row.get("macd_line")), 2),
            "macd_signal": safe_round(_f(row.get("macd_signal")), 2),
            "macd_hist":   safe_round(_f(row.get("macd_hist")), 2),
            "bb_squeeze":  bool(row.get("bb_squeeze")),
        },
        "ma_values": {
            f"MA_{p}": safe_round(_f(row.get(f"MA_{p}")), 0) for p in MA_PERIODS
        },
        "volume":          _i(row.get("Volume")),
        "tick_size":       get_tick_size(close) if close else None,
        "thresholds_used": {
            "tick_threshold": DEFAULT_TICK_THRESH,
            "vol_threshold":  DEFAULT_VOL_THRESH,
            "min_volume":     DEFAULT_MIN_VOLUME,
        },
        "disclaimer": _DISCLAIMER,
    }

    if signal:
        base["signal"]      = signal
        base["prediction"]  = _predict(signal, enriched)
        base["assessment"]  = "PASS — MA Ketat signal detected. Stock shows tight MA compression with sufficient volume."
    else:
        # Explain why it failed
        rt  = _f(row.get("range_ticks"))
        vp  = _f(row.get("vol_pct"))
        vol = _f(row.get("Volume")) or 0
        ma100 = _f(row.get("MA_100"))
        reasons = []
        if rt is not None and rt >= DEFAULT_TICK_THRESH:
            reasons.append(f"range_ticks={rt:.1f} ≥ {DEFAULT_TICK_THRESH} (MA lines too spread)")
        if vp is not None and vp >= DEFAULT_VOL_THRESH:
            reasons.append(f"vol_pct={vp:.1f}% ≥ {DEFAULT_VOL_THRESH}% (volatility too high)")
        if vol < DEFAULT_MIN_VOLUME:
            reasons.append(f"volume={vol:,.0f} < {DEFAULT_MIN_VOLUME:,} (insufficient liquidity)")
        if not _is_above_all_ma(row):
            reasons.append("price is below one or more MA lines")
        if close and ma100 and close < ma100:
            reasons.append(f"close={close} < MA100={ma100:.0f} (bearish macro bias)")
        base["assessment"] = "FAIL — " + "; ".join(reasons) if reasons else "FAIL — signal conditions not met."

    return base


async def get_prediction(ticker: str, horizon_days: int = 7) -> dict:
    """Get short-term MA Ketat directional prediction for a single stock.

    Args:
        ticker: IDX ticker symbol
        horizon_days: Forecast horizon (3–10 days, default 7)
    """
    try:
        normalized = validate_ticker(ticker)
    except ValueError as e:
        return {"error": True, "error_type": "invalid_ticker", "message": str(e),
                "partial_data": None, "suggestion": "Check the ticker symbol."}

    horizon_days = max(3, min(int(horizon_days or 7), 10))

    cached = cache.get("ma_predict", normalized, {"horizon": horizon_days})
    if cached is not None:
        return cached

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_predict_single, normalized, horizon_days),
            timeout=_T_ANALYZE,
        )
    except asyncio.TimeoutError:
        return {"error": True, "error_type": "timeout",
                "message": f"Prediction for {normalized} timed out.",
                "partial_data": None, "suggestion": "Try again."}
    except Exception as e:
        return {"error": True, "error_type": "data_unavailable",
                "message": f"Prediction failed for {normalized}: {e}",
                "partial_data": None, "suggestion": "Try again later."}

    if not result.get("error"):
        cache.set("ma_predict", normalized, result, _TTL_PREDICT, {"horizon": horizon_days})
    return result


def _predict_single(normalized: str, horizon: int) -> dict:
    data = _download_batch([_to_jk(normalized)], period="1y")
    df   = data.get(normalized)
    if df is None or df.empty:
        return {"error": True, "error_type": "data_unavailable",
                "message": f"No data for {normalized}.", "partial_data": None,
                "suggestion": "Verify ticker is active on BEI."}

    enriched = _enrich_df(df)
    signal   = _build_signal(normalized, enriched)

    if signal is None:
        return {
            "ticker":      normalized,
            "note":        "Stock does not currently show a MA Ketat signal. Prediction is less reliable.",
            "prediction":  None,
            "disclaimer":  _DISCLAIMER,
        }

    pred = _predict(signal, enriched, horizon)
    return {
        "ticker":      normalized,
        "close":       signal["close"],
        "scan_date":   now_wib().strftime("%Y-%m-%d"),
        "prediction":  pred,
        "signal_summary": {
            "confidence_score": signal["confidence_score"],
            "range_ticks":      signal["range_ticks"],
            "vol_pct":          signal["vol_pct"],
            "rsi":              signal["rsi"],
            "macd_crossover":   signal["macd_crossover"],
        },
        "disclaimer": _DISCLAIMER,
    }


# run_backtest and _backtest_single lived here. They backtested MA Ketat
# only, and src/tools/backtest.py now replays every strategy -- MA Ketat
# included -- through this module's own _build_signal. Keeping a second
# MA-Ketat backtest would be two implementations of one measurement, free
# to disagree after any edit to the filters.


async def get_scan_summary() -> dict:
    """Return a natural language summary of today's MA Ketat scan results.

    Uses cached scan data if available. Designed for direct LLM consumption.
    """
    full = await scan_today()
    if full.get("error"):
        return full

    top    = full.get("top_10", [])
    meta   = full.get("meta", {})
    n_sig  = meta.get("total_signals_found", 0)
    n_scan = meta.get("total_tickers_scanned", 0)

    # Sector breakdown
    sector_note = _sector_context(top)

    bullets = []
    for s in top[:5]:
        bullets.append(
            f"#{s.get('rank', '?')} {s['ticker']} — score {s['confidence_score']}/100, "
            f"ticks={s.get('range_ticks')}, RSI={s.get('rsi')}, "
            f"MACD {'✓' if s.get('macd_crossover') else '—'}"
        )

    return {
        "summary":         full.get("summary_text"),
        "scan_date":       meta.get("scan_date"),
        "total_scanned":   n_scan,
        "total_signals":   n_sig,
        "top_5_bullets":   bullets,
        "sector_context":  sector_note,
        "full_top_10":     top,
        "disclaimer":      _DISCLAIMER,
        "generated_at":    full.get("generated_at"),
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sector_context(signals: list[dict]) -> str:
    """Very simple sector heuristic based on known ticker prefixes."""
    banks   = {"BBCA", "BBRI", "BMRI", "BBNI", "BBTN", "BNGA", "NISP", "BJBR", "BJTM", "MEGA"}
    mining  = {"PTBA", "ADRO", "ITMG", "HRUM", "INDY", "BUMI", "TOBA", "MBAP"}
    consumer = {"ICBP", "INDF", "UNVR", "KLBF", "KAEF", "MYOR", "ULTJ", "SIDO"}

    counts: dict[str, int] = {"Banking": 0, "Mining": 0, "Consumer": 0, "Other": 0}
    for s in signals:
        t = s.get("ticker", "")
        if t in banks:
            counts["Banking"] += 1
        elif t in mining:
            counts["Mining"] += 1
        elif t in consumer:
            counts["Consumer"] += 1
        else:
            counts["Other"] += 1

    dominant = max(counts, key=lambda k: counts[k])
    n = counts[dominant]
    if n == 0:
        return "Signal distribution is diversified across sectors."
    if dominant == "Other":
        return f"{n} of the top signals are in varied/unclassified sectors."
    return f"{n} of the top signals are in the {dominant} sector."
