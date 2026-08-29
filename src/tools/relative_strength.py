"""Relative Strength Scanner — performance measured against the IHSG.

Every other scanner in this package judges a stock purely on its own chart. That
misses the question that decides most IDX trades: is this thing actually leading
the index, or is it merely drifting up on a day the whole market is up?

A setup that scores 76 on chart structure while lagging the IHSG by two
percentage points is a weak hand in a strong market. This scan makes that
visible, and doubles as a filter layer over the other nine strategies.

Excess return is arithmetic (stock return minus index return over the same
bars), which is what a trader is comparing when they say a stock "beat the
market this month". The RS line — price divided by the index — is tracked
separately, because a stock can post a positive excess return while its RS line
is still well below where it stood three months ago.
"""

import asyncio
import logging

import numpy as np
import pandas as pd

from ..utils.cache import cache
from ..utils.formatting import safe_round
from ._scan_common import (
    Funnel,
    build_envelope,
    elapsed_since,
    log_ticker_failure,
    scan_timer,
)
from .scanner import _download_batch, _f
from .universe import load_universe, universe_size

logger = logging.getLogger("idx-mcp.tools.relative_strength")

STRATEGY = "relative_strength"
BENCHMARK = "^JKSE"  # IHSG — the IDX composite index

DEFAULT_MIN_VOLUME = 500_000
DEFAULT_MIN_EXCESS_3M_PCT = 5.0
DEFAULT_REQUIRE_RS_HIGH = False

SCAN_PERIOD = "1y"
MIN_ROWS = 130          # needs a full 6-month lookback plus room for the RS line
BARS_1M, BARS_3M, BARS_6M = 21, 63, 126
RS_HIGH_LOOKBACK = 63   # RS line judged against its own 3-month high
RS_HIGH_TOLERANCE = 0.995
BETA_WINDOW = 63

_TTL_SCAN = 14_400
_TTL_BENCHMARK = 14_400
_T_SCAN = 150.0


def _benchmark_close() -> pd.Series | None:
    """IHSG closes, cached alongside the universe so one scan costs one fetch."""
    cached = cache.get("benchmark_close", BENCHMARK, {"period": SCAN_PERIOD})
    if cached is not None:
        return cached

    data = _download_batch([BENCHMARK], period=SCAN_PERIOD, min_rows=MIN_ROWS)
    df = data.get(BENCHMARK)
    if df is None or df.empty:
        logger.warning("benchmark %s unavailable — relative strength cannot be computed", BENCHMARK)
        return None

    close = df["Close"]
    cache.set("benchmark_close", BENCHMARK, close, _TTL_BENCHMARK, {"period": SCAN_PERIOD})
    return close


def _pct_return(series: pd.Series, bars: int) -> float | None:
    """Percentage return over the trailing ``bars`` sessions."""
    if len(series) <= bars:
        return None
    past = _f(series.iloc[-1 - bars])
    now = _f(series.iloc[-1])
    if past is None or now is None or past <= 0:
        return None
    return (now / past - 1.0) * 100.0


def _beta(stock_ret: pd.Series, index_ret: pd.Series, window: int = BETA_WINDOW) -> float | None:
    """Rolling-window beta of the stock against the index."""
    s = stock_ret.tail(window)
    i = index_ret.tail(window)
    if len(s) < window // 2:
        return None
    var = i.var()
    if var is None or not np.isfinite(var) or var == 0:
        return None
    return _f(s.cov(i) / var)


FUNNEL_STAGES = (
    "enough_history",
    "passed_volume_floor",
    "benchmark_aligned",
    "excess_3m_met",
    "rs_high_met",
)


def _build_signal(
    ticker_clean: str,
    df: pd.DataFrame,
    bench: pd.Series,
    min_vol: int,
    min_excess_3m: float,
    require_rs_high: bool,
    funnel: Funnel | None = None,
) -> dict | None:
    if df is None or len(df) < MIN_ROWS:
        return None
    if funnel:
        funnel.passed("enough_history")

    close = df["Close"]
    vol = _f(df["Volume"].iloc[-1]) or 0.0
    if vol < min_vol:
        return None
    if funnel:
        funnel.passed("passed_volume_floor")

    # Align the index onto the stock's own trading calendar. Forward-filling
    # covers the rare session a stock trades while the index print is missing;
    # leading NaNs are dropped rather than filled, so no return is computed
    # against a benchmark value that did not exist yet.
    idx_close = bench.reindex(close.index).ffill()
    valid = idx_close.notna()
    close, idx_close = close[valid], idx_close[valid]
    if len(close) < MIN_ROWS:
        return None

    stock_1m, stock_3m, stock_6m = (_pct_return(close, b) for b in (BARS_1M, BARS_3M, BARS_6M))
    idx_1m, idx_3m, idx_6m = (_pct_return(idx_close, b) for b in (BARS_1M, BARS_3M, BARS_6M))
    if None in (stock_1m, stock_3m, idx_1m, idx_3m):
        return None
    if funnel:
        funnel.passed("benchmark_aligned")

    excess_1m = stock_1m - idx_1m
    excess_3m = stock_3m - idx_3m
    excess_6m = (stock_6m - idx_6m) if None not in (stock_6m, idx_6m) else None

    if excess_3m < min_excess_3m:
        return None
    if funnel:
        funnel.passed("excess_3m_met")

    rs_line = close / idx_close.replace(0, np.nan)
    rs_now = _f(rs_line.iloc[-1])
    rs_peak = _f(rs_line.tail(RS_HIGH_LOOKBACK).max())
    if rs_now is None or rs_peak is None or rs_peak <= 0:
        return None
    rs_at_high = rs_now >= rs_peak * RS_HIGH_TOLERANCE

    if require_rs_high and not rs_at_high:
        return None
    if funnel:
        funnel.passed("rs_high_met")

    beta = _beta(close.pct_change().dropna(), idx_close.pct_change().dropna())

    score = 0.0
    score += 40 if excess_3m >= 20 else 30 if excess_3m >= 10 else 20 if excess_3m >= 5 else 10
    score += 25 if excess_1m >= 10 else 18 if excess_1m >= 5 else 10 if excess_1m > 0 else 0
    score += 20 if rs_at_high else 0
    # Outperforming a falling market is real, but it is not a long entry.
    score += 15 if stock_3m > 0 else 0

    return {
        "ticker": ticker_clean,
        "close": _f(close.iloc[-1]),
        "return_1m_pct": safe_round(stock_1m, 2),
        "return_3m_pct": safe_round(stock_3m, 2),
        "return_6m_pct": safe_round(stock_6m, 2),
        "ihsg_return_1m_pct": safe_round(idx_1m, 2),
        "ihsg_return_3m_pct": safe_round(idx_3m, 2),
        "excess_1m_pct": safe_round(excess_1m, 2),
        "excess_3m_pct": safe_round(excess_3m, 2),
        "excess_6m_pct": safe_round(excess_6m, 2),
        "rs_line": safe_round(rs_now, 6),
        "rs_line_3mo_high": safe_round(rs_peak, 6),
        "rs_at_3mo_high": rs_at_high,
        "beta_63d": safe_round(beta, 2),
        "volume": int(vol),
        "confidence_score": min(score, 100.0),
    }


def _run_full_scan(
    min_vol: int, min_excess_3m: float, require_rs_high: bool, top_n: int = 10
) -> dict:
    started = scan_timer()
    bench = _benchmark_close()
    if bench is None:
        return {
            "error": True,
            "error_type": "benchmark_unavailable",
            "message": f"Could not fetch {BENCHMARK} (IHSG); relative strength needs the index.",
            "suggestion": "Retry shortly — this is an upstream data outage, not a filter issue.",
        }

    all_data = load_universe(period=SCAN_PERIOD)
    funnel = Funnel(*FUNNEL_STAGES)

    signals = []
    for ticker_clean, df in all_data.items():
        try:
            signal = _build_signal(
                ticker_clean, df, bench, min_vol, min_excess_3m, require_rs_high, funnel
            )
            if signal:
                signals.append(signal)
        except Exception as e:
            log_ticker_failure(logger, ticker_clean, e)

    signals.sort(key=lambda x: x["confidence_score"], reverse=True)
    total = universe_size()
    logger.info(
        "relative_strength scan: %d/%d tickers with data, %d signals",
        len(all_data), total, len(signals),
    )

    return build_envelope(
        strategy=STRATEGY,
        signals=signals,
        total_scanned=total,
        downloaded=len(all_data),
        failed=total - len(all_data),
        filters={"min_volume": min_vol, "min_excess_3m_pct": min_excess_3m,
                 "require_rs_high": require_rs_high, "benchmark": BENCHMARK},
        elapsed_s=elapsed_since(started),
        top_n=top_n,
        funnel=funnel,
    )


async def scan_relative_strength(
    min_volume: int = DEFAULT_MIN_VOLUME,
    min_excess_3m_pct: float = DEFAULT_MIN_EXCESS_3M_PCT,
    require_rs_high: bool = DEFAULT_REQUIRE_RS_HIGH,
) -> dict:
    """Rank BEI stocks by outperformance against the IHSG."""
    try:
        min_volume = int(min_volume)
        min_excess_3m_pct = float(min_excess_3m_pct)
        require_rs_high = bool(require_rs_high)
    except (TypeError, ValueError):
        return {"error": True, "message": "Scan parameters must be numeric."}

    cache_key = f"rs_{min_volume}_{min_excess_3m_pct}_{require_rs_high}"
    cached = cache.get(STRATEGY, cache_key)
    if cached is not None:
        return cached

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                _run_full_scan, min_volume, min_excess_3m_pct, require_rs_high, 10
            ),
            timeout=_T_SCAN,
        )
    except asyncio.TimeoutError:
        return {
            "error": True,
            "error_type": "timeout",
            "message": f"Relative strength scan exceeded {_T_SCAN:.0f}s.",
            "suggestion": "Retry — upstream price data was slow. Results cache for 4h once complete.",
        }
    except Exception as e:
        logger.exception("relative_strength scan failed")
        return {"error": True, "error_type": "scan_failed", "message": str(e)}

    if not result.get("error"):
        cache.set(STRATEGY, cache_key, result, _TTL_SCAN)
    return result
