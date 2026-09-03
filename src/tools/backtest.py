"""Historical base rates for every scan strategy.

`run_backtest` covered MA Ketat alone, so nine of the ten scans shipped with no
evidence at all: a signal could be produced daily and nothing in the system
could say whether that signal had ever been worth acting on.

The central design constraint is that a backtest must exercise *the same code
the live scan runs*. Reimplementing each filter as a vectorised expression is
far faster, but it measures a strategy that is not the one in production, and
the two drift apart silently the moment either is edited. So each strategy is
replayed through its own ``_build_signal`` -- the exact function the scanner
calls -- one session at a time.

Two properties make that affordable and honest:

* Every indicator in every ``_enrich_df`` is causal (rolling and shift only; no
  ``expanding``, no negative shifts, no whole-series extrema). Indicators are
  therefore computed once over the full history and the frame is *sliced*,
  rather than being recomputed per session. Slicing an acausal indicator would
  leak the future into every bar, so this property is asserted by a test rather
  than assumed.
* ``_build_signal`` reads ``df.iloc[-1]``, so a slice ending at session *i* is
  exactly what the scanner would have seen on that day.

Outcome measurement stays deliberately simple and identical across strategies:
enter at the close of the signal bar, exit at the close *horizon* sessions
later. That is a base rate, not a trading simulation -- it has no stops and no
targets, which is what makes strategies comparable to each other. Trade-level
outcomes with real levels are what ``evaluate_predictions`` is for.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from ..utils.cache import cache
from ..utils.ticker import validate_ticker
from . import (
    breakout_high,
    distribution,
    gap,
    golden_cross,
    mean_reversion,
    relative_strength,
    scanner,
    trend_pullback,
    vol_squeeze,
    volume_accumulation,
)

logger = logging.getLogger("idx-mcp.tools.backtest")

DISCLAIMER = (
    "This output is for educational and analytical purposes only. "
    "Not financial advice. Trading decisions remain the user's full responsibility."
)

DEFAULT_HORIZON = 7
MAX_HORIZON = 30
_TIMEOUT = 120.0
_TTL = 3_600

#: A risk scan flags weakness, so a *fall* after the signal is the strategy
#: being right. Scoring it like a long entry would report a working warning
#: system as a failing one.
LONG, RISK = "long", "risk"


@dataclass(frozen=True)
class Strategy:
    """How to replay one scan over history."""

    name: str
    module: object
    build: Callable
    param_names: tuple[str, ...]
    defaults: dict
    min_rows: int
    direction: str = LONG
    enrich: Callable | None = None
    needs_benchmark: bool = False
    #: Reads the traded direction off the signal itself where the strategy can
    #: emit either (scan_gap), so a gap-down is not scored as a failed long.
    direction_field: str | None = None
    extra_enrich_params: tuple[str, ...] = field(default_factory=tuple)


REGISTRY: dict[str, Strategy] = {
    "scan_ma_breakout": Strategy(
        name="scan_ma_breakout",
        module=scanner,
        enrich=scanner._enrich_df,
        build=scanner._build_signal,
        param_names=("tick_threshold", "vol_threshold"),
        defaults={"tick_threshold": scanner.DEFAULT_TICK_THRESH,
                  "vol_threshold": scanner.DEFAULT_VOL_THRESH},
        min_rows=110,
    ),
    "scan_golden_cross": Strategy(
        name="scan_golden_cross",
        module=golden_cross,
        enrich=golden_cross._enrich_df,
        build=golden_cross._build_gc_signal,
        param_names=("stoch_threshold", "min_volume"),
        defaults={"stoch_threshold": golden_cross.DEFAULT_STOCH_THRESH,
                  "min_volume": golden_cross.DEFAULT_MIN_VOLUME},
        min_rows=golden_cross.MIN_ROWS,
    ),
    "scan_mean_reversion": Strategy(
        name="scan_mean_reversion",
        module=mean_reversion,
        enrich=mean_reversion._enrich_df,
        build=mean_reversion._build_signal,
        param_names=("rsi_threshold", "min_volume", "min_below_sma20_pct"),
        defaults={"rsi_threshold": mean_reversion.DEFAULT_RSI_THRESH,
                  "min_volume": mean_reversion.DEFAULT_MIN_VOLUME,
                  "min_below_sma20_pct": mean_reversion.DEFAULT_MIN_BELOW_SMA20_PCT},
        min_rows=mean_reversion.MIN_ROWS,
    ),
    "scan_volatility_squeeze": Strategy(
        name="scan_volatility_squeeze",
        module=vol_squeeze,
        enrich=vol_squeeze._enrich_df,
        build=vol_squeeze._build_signal,
        param_names=("min_volume", "squeeze_tolerance"),
        defaults={"min_volume": vol_squeeze.DEFAULT_MIN_VOLUME,
                  "squeeze_tolerance": vol_squeeze.DEFAULT_SQUEEZE_TOLERANCE},
        min_rows=vol_squeeze.MIN_ROWS,
    ),
    "scan_volume_accumulation": Strategy(
        name="scan_volume_accumulation",
        module=volume_accumulation,
        enrich=volume_accumulation._enrich_df,
        build=volume_accumulation._build_signal,
        param_names=("min_volume", "vol_multiple", "max_spread_pct"),
        defaults={"min_volume": volume_accumulation.DEFAULT_MIN_VOLUME,
                  "vol_multiple": volume_accumulation.DEFAULT_VOL_MULTIPLE,
                  "max_spread_pct": volume_accumulation.DEFAULT_MAX_SPREAD_PCT},
        min_rows=volume_accumulation.MIN_ROWS,
    ),
    "scan_relative_strength": Strategy(
        name="scan_relative_strength",
        module=relative_strength,
        enrich=None,                       # works off raw closes plus the index
        build=relative_strength._build_signal,
        param_names=("min_volume", "min_excess_3m_pct", "require_rs_high"),
        defaults={"min_volume": relative_strength.DEFAULT_MIN_VOLUME,
                  "min_excess_3m_pct": relative_strength.DEFAULT_MIN_EXCESS_3M_PCT,
                  "require_rs_high": relative_strength.DEFAULT_REQUIRE_RS_HIGH},
        min_rows=relative_strength.MIN_ROWS,
        needs_benchmark=True,
    ),
    "scan_trend_pullback": Strategy(
        name="scan_trend_pullback",
        module=trend_pullback,
        enrich=trend_pullback._enrich_df,
        build=trend_pullback._build_signal,
        param_names=("min_volume", "rsi_min", "rsi_max", "max_pullback_pct"),
        defaults={"min_volume": trend_pullback.DEFAULT_MIN_VOLUME,
                  "rsi_min": trend_pullback.DEFAULT_RSI_MIN,
                  "rsi_max": trend_pullback.DEFAULT_RSI_MAX,
                  "max_pullback_pct": trend_pullback.DEFAULT_MAX_PULLBACK_PCT},
        min_rows=trend_pullback.MIN_ROWS,
    ),
    "scan_breakout_high": Strategy(
        name="scan_breakout_high",
        module=breakout_high,
        enrich=breakout_high._enrich_df,
        build=breakout_high._build_signal,
        param_names=("min_volume", "vol_multiple", "max_base_range_pct"),
        defaults={"min_volume": breakout_high.DEFAULT_MIN_VOLUME,
                  "vol_multiple": breakout_high.DEFAULT_VOL_MULTIPLE,
                  "max_base_range_pct": breakout_high.DEFAULT_MAX_BASE_RANGE_PCT,
                  "lookback_days": breakout_high.DEFAULT_LOOKBACK_DAYS},
        min_rows=breakout_high.MIN_ROWS,
        extra_enrich_params=("lookback_days",),
    ),
    "scan_distribution_warning": Strategy(
        name="scan_distribution_warning",
        module=distribution,
        enrich=distribution._enrich_df,
        build=distribution._build_signal,
        param_names=("min_volume", "min_warning_score"),
        defaults={"min_volume": distribution.DEFAULT_MIN_VOLUME,
                  "min_warning_score": distribution.DEFAULT_MIN_WARNING_SCORE},
        min_rows=distribution.MIN_ROWS,
        direction=RISK,
    ),
    "scan_gap": Strategy(
        name="scan_gap",
        module=gap,
        enrich=gap._enrich_df,
        build=gap._build_signal,
        param_names=("min_volume", "min_gap_pct", "direction"),
        defaults={"min_volume": gap.DEFAULT_MIN_VOLUME,
                  "min_gap_pct": gap.DEFAULT_MIN_GAP_PCT,
                  "direction": gap.DEFAULT_DIRECTION},
        min_rows=gap.MIN_ROWS,
        direction_field="direction",
    ),
}

STRATEGY_NAMES = tuple(REGISTRY)


def _f(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(out) else out


def _signal_is_bullish(signal: dict, strategy: Strategy) -> bool:
    """Which way this particular signal expects price to go."""
    if strategy.direction_field:
        declared = str(signal.get(strategy.direction_field, "")).lower()
        if declared in ("down", "short", "bearish"):
            return False
        if declared in ("up", "long", "bullish"):
            return True
    return strategy.direction == LONG


def replay(
    strategy: Strategy,
    ticker: str,
    df: pd.DataFrame,
    options: dict,
    horizon: int,
    benchmark: pd.Series | None = None,
) -> list[dict]:
    """Walk history one session at a time through the live signal builder.

    Indicators are enriched once over the whole frame and then sliced. That is
    only sound because every enrichment is causal; see the module docstring.
    """
    enrich_args = [options[name] for name in strategy.extra_enrich_params]
    enriched = strategy.enrich(df, *enrich_args) if strategy.enrich else df.copy()
    params = tuple(options[name] for name in strategy.param_names)

    closes = enriched["Close"]
    out: list[dict] = []

    for i in range(strategy.min_rows, len(enriched) - horizon):
        window = enriched.iloc[: i + 1]
        try:
            if strategy.needs_benchmark:
                signal = strategy.build(ticker, window, benchmark, *params)
            else:
                signal = strategy.build(ticker, window, *params)
        except Exception as e:                       # a thin bar, not a bug
            logger.debug("%s replay skipped %s at %d: %s", strategy.name, ticker, i, e)
            continue
        if not signal:
            continue

        entry = _f(closes.iloc[i])
        exit_price = _f(closes.iloc[i + horizon])
        if entry is None or exit_price is None or entry <= 0:
            continue

        raw_pct = (exit_price - entry) / entry * 100.0
        bullish = _signal_is_bullish(signal, strategy)
        # For a risk signal the strategy is right when price falls, so the
        # return is reported from the strategy's point of view.
        directional = raw_pct if bullish else -raw_pct

        try:
            date_str = str(enriched.index[i].date())
        except AttributeError:
            date_str = str(enriched.index[i])

        out.append({
            "date": date_str,
            "close": round(entry, 4),
            "exit_close": round(exit_price, 4),
            "price_change_pct": round(raw_pct, 2),
            "strategy_return_pct": round(directional, 2),
            "expected_direction": "up" if bullish else "down",
            "win": directional > 0,
            "confidence_score": signal.get("confidence_score"),
        })
    return out


def summarise(trades: list[dict], horizon: int) -> dict:
    """Aggregate a replay. Returns are always from the strategy's point of view."""
    if not trades:
        return {
            "n_signals": 0,
            "win_rate_pct": None,
            "avg_return_pct": None,
            "median_return_pct": None,
            "best_return_pct": None,
            "worst_return_pct": None,
            "avg_hold_days": horizon,
        }

    returns = np.array([t["strategy_return_pct"] for t in trades], dtype=float)
    wins = int((returns > 0).sum())
    return {
        "n_signals": len(trades),
        "win_rate_pct": round(wins / len(returns) * 100, 1),
        "avg_return_pct": round(float(returns.mean()), 2),
        "median_return_pct": round(float(np.median(returns)), 2),
        "best_return_pct": round(float(returns.max()), 2),
        "worst_return_pct": round(float(returns.min()), 2),
        "avg_hold_days": horizon,
    }


def _resolve_options(strategy: Strategy, overrides: dict | None) -> dict:
    options = dict(strategy.defaults)
    for key, value in (overrides or {}).items():
        if key in options and value is not None:
            options[key] = value
    return options


def _backtest_sync(
    normalized: str, strategy_names: list[str], period: str,
    horizon: int, overrides: dict | None,
) -> dict:
    frames = scanner._download_batch([scanner._to_jk(normalized)], period=period)
    df = frames.get(normalized)
    if df is None or df.empty:
        return {"error": True, "error_type": "data_unavailable",
                "message": f"No price history for {normalized}.",
                "partial_data": None, "suggestion": "Check the ticker symbol."}

    benchmark = None
    if any(REGISTRY[s].needs_benchmark for s in strategy_names):
        benchmark = relative_strength._benchmark_close()

    per_strategy: dict[str, dict] = {}
    for name in strategy_names:
        strategy = REGISTRY[name]
        options = _resolve_options(strategy, overrides)

        # A strategy needing more warm-up than the fetch provides can never fire.
        # Saying so beats reporting a truthful-looking zero.
        needed = strategy.min_rows + horizon + 1
        if len(df) < needed:
            per_strategy[name] = {
                "n_signals": 0, "win_rate_pct": None, "avg_return_pct": None,
                "not_evaluated": True,
                "note": (f"needs {needed} sessions ({strategy.min_rows} warm-up "
                         f"+ {horizon} horizon); only {len(df)} available"),
            }
            continue
        if strategy.needs_benchmark and benchmark is None:
            per_strategy[name] = {
                "n_signals": 0, "win_rate_pct": None, "avg_return_pct": None,
                "not_evaluated": True,
                "note": "benchmark (^JKSE) history unavailable",
            }
            continue

        trades = replay(strategy, normalized, df, options, horizon, benchmark)
        summary = summarise(trades, horizon)
        summary["direction"] = strategy.direction
        summary["filters_applied"] = options
        summary["trades"] = trades[-20:]
        if trades:
            summary["note"] = None
        else:
            summary["note"] = "no historical signals in this period"
        per_strategy[name] = summary

    evaluated = {k: v for k, v in per_strategy.items() if not v.get("not_evaluated")}
    fired = {k: v for k, v in evaluated.items() if v["n_signals"]}

    # When one strategy is asked for, its headline numbers are also promoted to
    # the top level. `run_backtest` used to be MA-Ketat-only with a flat shape,
    # and the skill templates read win_rate_pct from there; a bare KeyError is a
    # worse answer than the number they were looking for.
    flat: dict = {}
    if len(strategy_names) == 1:
        only = per_strategy[strategy_names[0]]
        flat = {k: only.get(k) for k in
                ("n_signals", "win_rate_pct", "avg_return_pct",
                 "median_return_pct", "best_return_pct", "worst_return_pct",
                 "avg_hold_days")}
    else:
        # Explicit nulls rather than absent keys, for the same reason.
        flat = {k: None for k in
                ("n_signals", "win_rate_pct", "avg_return_pct",
                 "median_return_pct", "best_return_pct", "worst_return_pct")}
        flat["avg_hold_days"] = horizon
        flat["note"] = (
            "several strategies were run; per-strategy figures are in by_strategy. "
            "Pass `strategy` to get one strategy's numbers at the top level."
        )

    return {
        **flat,
        "ticker": normalized,
        "period": period,
        "horizon_days": horizon,
        "sessions_available": len(df),
        "strategies_requested": len(strategy_names),
        "strategies_evaluated": len(evaluated),
        "strategies_with_signals": len(fired),
        "by_strategy": per_strategy,
        "interpretation": (
            "Entry at the signal bar's close, exit at the close "
            f"{horizon} sessions later -- a base rate, with no stops or targets, "
            "so strategies stay comparable. strategy_return_pct is signed from "
            "the strategy's point of view: for a risk scan a falling price is a "
            "win. A small n_signals is not evidence; treat anything under ~20 as "
            "an anecdote."
        ),
        "disclaimer": DISCLAIMER,
    }


async def run_backtest_all(
    ticker: str,
    strategy: str | None = None,
    period: str = "2y",
    horizon_days: int = DEFAULT_HORIZON,
    **overrides,
) -> dict:
    """Backtest one or every scan strategy on a ticker's history."""
    try:
        normalized = validate_ticker(ticker)
    except ValueError as e:
        return {"error": True, "error_type": "invalid_ticker", "message": str(e),
                "partial_data": None, "suggestion": "Check the ticker symbol."}

    if strategy and strategy not in REGISTRY:
        return {"error": True, "error_type": "invalid_arguments",
                "message": (f"Unknown strategy {strategy!r}. "
                            f"Choose one of: {', '.join(STRATEGY_NAMES)}."),
                "partial_data": None, "suggestion": "Omit it to backtest all ten."}

    names = [strategy] if strategy else list(STRATEGY_NAMES)
    period = period if period in ("1y", "2y", "5y") else "2y"
    try:
        horizon = int(horizon_days)
    except (TypeError, ValueError):
        horizon = DEFAULT_HORIZON
    horizon = max(1, min(horizon, MAX_HORIZON))

    key_params = {"s": strategy or "all", "p": period, "h": horizon,
                  **{k: v for k, v in sorted(overrides.items()) if v is not None}}
    cached = cache.get("backtest_all", normalized, key_params)
    if cached is not None:
        return cached

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_backtest_sync, normalized, names, period, horizon, overrides),
            timeout=_TIMEOUT,
        )
    except asyncio.TimeoutError:
        return {"error": True, "error_type": "timeout",
                "message": f"Backtest for {normalized} timed out after {_TIMEOUT:.0f}s.",
                "partial_data": None,
                "suggestion": "Backtest one strategy at a time, or use a shorter period."}
    except Exception as e:
        logger.exception("backtest failed for %s", normalized)
        return {"error": True, "error_type": "data_unavailable",
                "message": f"Backtest failed for {normalized}: {e}",
                "partial_data": None, "suggestion": "Try again later."}

    if not result.get("error"):
        cache.set("backtest_all", normalized, result, _TTL, key_params)
    return result
