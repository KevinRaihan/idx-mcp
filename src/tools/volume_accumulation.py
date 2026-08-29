"""Volume Accumulation Scanner.

Finds stocks that have traded 300%+ of their average 20-day volume today,
but the price range remains tight (accumulation without price explosion yet).
"""

import asyncio
import logging

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
from .scanner import _f, _load_tickers, _to_jk
from .universe import load_universe

logger = logging.getLogger("idx-mcp.tools.volume_accumulation")

STRATEGY = "volume_accumulation"
DEFAULT_MIN_VOLUME = 1_000_000
DEFAULT_VOL_MULTIPLE = 3.0
DEFAULT_MAX_SPREAD_PCT = 5.0
BATCH_SIZE = 80
# "1mo" only yields ~22 bars, which is below MIN_ROWS and leaves the 20-day
# volume average with a single valid point — the scan returned nothing at all.
SCAN_PERIOD = "3mo"
MIN_ROWS = 25
_TTL_SCAN = 14_400
_T_SCAN = 150.0


def _enrich_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    # Exclude the current bar from its own baseline, otherwise a volume spike
    # inflates the average it is being measured against.
    df["vol_20d_avg"] = df["Volume"].shift(1).rolling(20).mean()
    df["price_spread_pct"] = (df["High"] - df["Low"]) / df["Close"] * 100
    return df


FUNNEL_STAGES = (
    "enough_history",
    "passed_volume_floor",
    "volume_multiple_met",
    "spread_tight_enough",
    "close_held_up",
)


def _passes_entry_filters(
    row: pd.Series,
    prev_close: float | None,
    min_vol: int,
    vol_multiple: float,
    max_spread_pct: float,
    funnel: Funnel | None = None,
) -> bool:
    close = _f(row.get("Close"))
    vol = _f(row.get("Volume"))
    vol_avg = _f(row.get("vol_20d_avg"))
    spread = _f(row.get("price_spread_pct"))

    if None in (close, vol, vol_avg, spread, prev_close):
        return False
    if vol_avg <= 0 or close <= 0:
        return False

    if vol < min_vol:
        return False
    if funnel:
        funnel.passed("passed_volume_floor")

    if (vol / vol_avg) < vol_multiple:
        return False
    if funnel:
        funnel.passed("volume_multiple_met")

    if spread > max_spread_pct:      # tight intraday range
        return False
    if funnel:
        funnel.passed("spread_tight_enough")

    if close < prev_close:           # accumulation, not distribution
        return False
    if funnel:
        funnel.passed("close_held_up")

    return True


def _build_signal(
    ticker_clean: str,
    df: pd.DataFrame,
    min_vol: int,
    vol_multiple: float,
    max_spread_pct: float,
    funnel: Funnel | None = None,
) -> dict | None:
    if df is None or len(df) < MIN_ROWS:
        return None
    if funnel:
        funnel.passed("enough_history")

    row = df.iloc[-1]
    prev_close = _f(df["Close"].iloc[-2])
    if not _passes_entry_filters(
        row, prev_close, min_vol, vol_multiple, max_spread_pct, funnel
    ):
        return None

    close = _f(row.get("Close"))
    vol = _f(row.get("Volume")) or 0.0
    vol_avg = _f(row.get("vol_20d_avg"))
    vol_ratio = vol / vol_avg
    spread = _f(row.get("price_spread_pct"))
    change_pct = (close - prev_close) / prev_close * 100 if prev_close else 0.0

    score = 0.0
    score += 50 if vol_ratio > 5.0 else 30 if vol_ratio > 4.0 else 20
    score += 30 if spread < 2.0 else 20 if spread < 3.0 else 10
    score += 20 if change_pct > 2.0 else 10

    return {
        "ticker": ticker_clean,
        "close": close,
        "prev_close": prev_close,
        "change_pct": safe_round(change_pct, 2),
        "volume": int(vol),
        "avg_volume_20d": int(vol_avg),
        "volume_ratio": safe_round(vol_ratio, 2),
        "intraday_spread_pct": safe_round(spread, 2),
        "confidence_score": min(score, 100.0),
    }


def _run_full_scan(
    min_vol: int, vol_multiple: float, max_spread_pct: float, top_n: int = 10
) -> dict:
    started = scan_timer()
    tickers = _load_tickers()
    jk_list = [_to_jk(t) for t in tickers]

    # One shared universe fetch backs every scanner; see tools/universe.py.
    all_data = load_universe(period=SCAN_PERIOD)
    funnel = Funnel(*FUNNEL_STAGES)

    signals = []
    for ticker_clean, df in all_data.items():
        try:
            signal = _build_signal(
                ticker_clean, _enrich_df(df), min_vol, vol_multiple, max_spread_pct, funnel
            )
            if signal:
                signals.append(signal)
        except Exception as e:
            log_ticker_failure(logger, ticker_clean, e)

    signals.sort(key=lambda x: x["confidence_score"], reverse=True)
    logger.info(
        "volume_accumulation scan: %d/%d tickers with data, %d signals",
        len(all_data), len(jk_list), len(signals),
    )

    return build_envelope(
        strategy=STRATEGY,
        signals=signals,
        total_scanned=len(jk_list),
        downloaded=len(all_data),
        failed=len(jk_list) - len(all_data),
        filters={
            "min_volume": min_vol,
            "volume_multiple": vol_multiple,
            "max_intraday_spread_pct": max_spread_pct,
        },
        elapsed_s=elapsed_since(started),
        top_n=top_n,
        funnel=funnel,
    )


async def scan_volume_accumulation(
    min_volume: int = DEFAULT_MIN_VOLUME,
    vol_multiple: float = DEFAULT_VOL_MULTIPLE,
    max_spread_pct: float = DEFAULT_MAX_SPREAD_PCT,
) -> dict:
    """Run Volume Accumulation scan across the BEI universe."""
    try:
        min_volume = int(min_volume)
        vol_multiple = float(vol_multiple)
        max_spread_pct = float(max_spread_pct)
    except (TypeError, ValueError):
        return {"error": True, "message": "Scan parameters must be numeric."}
    if vol_multiple <= 0 or max_spread_pct <= 0:
        return {"error": True, "message": "vol_multiple and max_spread_pct must be positive."}

    cache_key = f"va_{min_volume}_{vol_multiple}_{max_spread_pct}"
    cached = cache.get(STRATEGY, cache_key)
    if cached is not None:
        return cached

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                _run_full_scan, min_volume, vol_multiple, max_spread_pct, 10
            ),
            timeout=_T_SCAN,
        )
    except asyncio.TimeoutError:
        return {
            "error": True,
            "error_type": "timeout",
            "message": f"Volume accumulation scan exceeded {_T_SCAN:.0f}s.",
            "suggestion": "Retry — upstream price data was slow. Results cache for 4h once complete.",
        }
    except Exception as e:
        logger.exception("volume_accumulation scan failed")
        return {"error": True, "error_type": "scan_failed", "message": str(e)}

    cache.set(STRATEGY, cache_key, result, _TTL_SCAN)
    return result
