"""Distribution Warning Scanner — the one scan that is not a buy list.

Every other strategy here hunts for entries. That left a real hole: nothing in
the package could answer "is anything I hold breaking down?", and nothing could
veto a long signal that another scan had produced on a chart that is quietly
falling apart.

IDX shorting is restricted for most participants, so this is framed as risk
rather than as a short setup. Two uses:

* run it against positions already held, as an exit trigger;
* intersect it with the long scans, and treat an overlap as a veto.

Scoring is additive over independent warning flags rather than a hard AND of all
of them. Breakdowns are not synchronised — volume distribution usually shows up
before the death cross, and demanding every symptom at once would only find
stocks that have already fallen too far to act on.

SMA200-dependent flags are skipped rather than failed when history is too short,
so a recently listed stock can still raise the warnings it has the data for.
"""

import asyncio
import logging

import numpy as np
import pandas as pd

from ..utils.cache import cache
from ..utils.formatting import safe_round
from ._scan_common import build_envelope, elapsed_since, log_ticker_failure, scan_timer
from .scanner import _f
from .universe import load_universe, universe_size

logger = logging.getLogger("idx-mcp.tools.distribution")

STRATEGY = "distribution_warning"
DEFAULT_MIN_VOLUME = 500_000
DEFAULT_MIN_WARNING_SCORE = 50.0
DISTRIBUTION_VOL_MULTIPLE = 1.3
SMA50_SLOPE_LOOKBACK = 20

SCAN_PERIOD = "2y"
MIN_ROWS = 80          # enough for SMA50 plus a 60-day swing-high comparison
_TTL_SCAN = 14_400
_T_SCAN = 150.0


def _ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False).mean()


def _enrich_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["SMA50"] = df["Close"].rolling(50).mean()
    # min_periods lets SMA200 stay NaN for short histories instead of raising;
    # the flags that use it are skipped in that case.
    df["SMA200"] = df["Close"].rolling(200).mean()
    df["SMA50_prev"] = df["SMA50"].shift(SMA50_SLOPE_LOOKBACK)

    macd_line = _ema(df["Close"], 12) - _ema(df["Close"], 26)
    df["macd_hist"] = macd_line - _ema(macd_line, 9)

    df["vol_20d_avg"] = df["Volume"].shift(1).rolling(20).mean()
    df["high_20d"] = df["High"].rolling(20).max()
    df["high_60d"] = df["High"].rolling(60).max()
    return df


def _build_signal(
    ticker_clean: str, df: pd.DataFrame, min_vol: int, min_score: float
) -> dict | None:
    if df is None or len(df) < MIN_ROWS:
        return None

    row, prev = df.iloc[-1], df.iloc[-2]
    close = _f(row.get("Close"))
    prev_close = _f(prev.get("Close"))
    vol = _f(row.get("Volume"))
    sma50 = _f(row.get("SMA50"))
    sma50_prev = _f(row.get("SMA50_prev"))
    sma200 = _f(row.get("SMA200"))
    macd_hist, macd_prev = _f(row.get("macd_hist")), _f(prev.get("macd_hist"))
    vol_avg = _f(row.get("vol_20d_avg"))
    high_20d, high_60d = _f(row.get("high_20d")), _f(row.get("high_60d"))

    if None in (close, prev_close, vol, sma50, high_20d, high_60d) or high_60d <= 0:
        return None
    if vol < min_vol:
        return None

    warnings: list[str] = []
    score = 0.0

    if close < sma50:
        score += 20
        warnings.append("close_below_sma50")

    if sma200 is not None and sma50 < sma200:
        score += 20
        warnings.append("death_cross")

    if sma50_prev is not None and sma50 < sma50_prev:
        score += 15
        warnings.append("sma50_declining")

    if None not in (macd_hist, macd_prev) and macd_hist < 0 and macd_hist < macd_prev:
        score += 15
        warnings.append("macd_negative_and_falling")

    # A 20-day high strictly below the 60-day high means the last month failed
    # to reach where the quarter did — a lower high on the swing chart.
    if high_20d < high_60d:
        score += 15
        warnings.append("lower_high")

    vol_ratio = (vol / vol_avg) if vol_avg else 0.0
    if close < prev_close and vol_ratio >= DISTRIBUTION_VOL_MULTIPLE:
        score += 15
        warnings.append("heavy_volume_down_day")

    if score < min_score:
        return None

    drawdown_pct = (high_60d - close) / high_60d * 100.0

    return {
        "ticker": ticker_clean,
        "close": close,
        "sma50": safe_round(sma50, 2),
        "sma200": safe_round(sma200, 2),
        "below_sma50": close < sma50,
        "death_cross": bool(sma200 is not None and sma50 < sma200),
        "macd_hist": safe_round(macd_hist, 4),
        "high_20d": safe_round(high_20d, 2),
        "high_60d": safe_round(high_60d, 2),
        "drawdown_from_60d_high_pct": safe_round(drawdown_pct, 2),
        "volume": int(vol),
        "volume_ratio": safe_round(vol_ratio, 2),
        "warnings": warnings,
        "warning_count": len(warnings),
        # Named confidence_score for envelope compatibility, but it ranks
        # severity: a higher number is a worse chart, not a better trade.
        "confidence_score": min(score, 100.0),
    }


def _run_full_scan(min_vol: int, min_score: float, top_n: int = 10) -> dict:
    started = scan_timer()
    all_data = load_universe(period=SCAN_PERIOD)

    signals = []
    for ticker_clean, df in all_data.items():
        try:
            signal = _build_signal(ticker_clean, _enrich_df(df), min_vol, min_score)
            if signal:
                signals.append(signal)
        except Exception as e:
            log_ticker_failure(logger, ticker_clean, e)

    signals.sort(key=lambda x: x["confidence_score"], reverse=True)
    total = universe_size()
    logger.info(
        "distribution scan: %d/%d tickers with data, %d warnings",
        len(all_data), total, len(signals),
    )

    envelope = build_envelope(
        strategy=STRATEGY,
        signals=signals,
        total_scanned=total,
        downloaded=len(all_data),
        failed=total - len(all_data),
        filters={"min_volume": min_vol, "min_warning_score": min_score},
        elapsed_s=elapsed_since(started),
        top_n=top_n,
    )
    envelope["interpretation"] = (
        "These are risk warnings, not entries. A high score means the chart is "
        "breaking down: use it to exit or trim an existing position, or to veto a "
        "long signal from another scan on the same ticker."
    )
    return envelope


async def scan_distribution_warning(
    min_volume: int = DEFAULT_MIN_VOLUME,
    min_warning_score: float = DEFAULT_MIN_WARNING_SCORE,
) -> dict:
    """Flag BEI stocks showing distribution and trend-breakdown symptoms."""
    try:
        min_volume = int(min_volume)
        min_warning_score = float(min_warning_score)
    except (TypeError, ValueError):
        return {"error": True, "message": "Scan parameters must be numeric."}
    if not 0 <= min_warning_score <= 100:
        return {"error": True, "message": "min_warning_score must be between 0 and 100."}

    cache_key = f"dw_{min_volume}_{min_warning_score}"
    cached = cache.get(STRATEGY, cache_key)
    if cached is not None:
        return cached

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_run_full_scan, min_volume, min_warning_score, 10),
            timeout=_T_SCAN,
        )
    except asyncio.TimeoutError:
        return {
            "error": True,
            "error_type": "timeout",
            "message": f"Distribution scan exceeded {_T_SCAN:.0f}s.",
            "suggestion": "Retry — upstream price data was slow. Results cache for 4h once complete.",
        }
    except Exception as e:
        logger.exception("distribution scan failed")
        return {"error": True, "error_type": "scan_failed", "message": str(e)}

    cache.set(STRATEGY, cache_key, result, _TTL_SCAN)
    return result
