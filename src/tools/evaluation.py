"""Forward-test evaluation — scoring logged theses against what actually happened.

The predictions log was write-only. ``evaluate_and_log_thesis`` appended to it,
nothing ever read it back, and no entry carried an outcome. Twenty-six theses
accumulated without a single one being scored, which meant every ``win_prob``
in the system was an assertion no evidence could contradict.

This module closes that loop. For each logged thesis it walks the daily bars
from the session after it was logged and asks which came first: the target or
the stop.

Two judgement calls are worth stating plainly.

*Same-bar ambiguity.* When one daily bar's high clears the target and its low
takes out the stop, daily data cannot say which happened first. That is
resolved as a stop — the pessimistic reading — and flagged, because a forward
test that resolves its own ambiguities favourably is not a test.

*Entry assumption.* Evaluation starts on the session **after** the log
timestamp. A thesis logged against today's close could not have been entered
until the next session, and scoring it from the same bar would let it use
information it did not have.
"""

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

from ..utils.formatting import safe_round
from ..utils.ohlcv import drop_incomplete_bars, drop_unsettled_session
from ..utils.paths import predictions_log_file
from ..utils.ticker import to_yfinance_ticker
from .predictions import (
    SCHEMA_VERSION,
    _atomic_write_json,
    _log_file_lock,
    _log_lock,
    _read_predictions_log,
)

logger = logging.getLogger("idx-mcp.tools.evaluation")

DEFAULT_BUY_FEE = 0.0015
DEFAULT_SELL_FEE = 0.0025
_T_EVALUATE = 120.0

# "ENTRY 1365 STOP 1315 TARGET 1450" — the shape written into free-text
# reasoning before the schema had fields for it.
_PROSE_LEVELS = re.compile(
    r"ENTRY\s+([\d.]+).*?STOP\s+([\d.]+).*?TARGET\s+([\d.]+)",
    re.IGNORECASE | re.DOTALL,
)


# ── level recovery for pre-v3 entries ─────────────────────────────────────────

def _levels_from_reasoning(entry: dict) -> tuple[float, float, float] | None:
    match = _PROSE_LEVELS.search(entry.get("reasoning") or "")
    if not match:
        return None
    try:
        e, s, t = (float(g) for g in match.groups())
    except ValueError:
        return None
    return (e, s, t) if s < e < t else None


def _levels_from_ratios(entry: dict, entry_price: float) -> tuple[float, float, float] | None:
    """Reconstruct stop and target from the logged IDR magnitudes.

    ``profit_target_idr / position_value_idr`` is the fractional move to target,
    independent of how many shares were bought, so the absolute levels follow
    once the entry price is known.
    """
    pos = entry.get("position_value_idr")
    profit = entry.get("profit_target_idr")
    loss = entry.get("loss_target_idr")
    if not pos or profit is None or loss is None or entry_price <= 0:
        return None
    try:
        target = entry_price * (1.0 + float(profit) / float(pos))
        stop = entry_price * (1.0 - float(loss) / float(pos))
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return (entry_price, stop, target) if stop < entry_price < target else None


def _logged_date(entry: dict) -> datetime | None:
    raw = entry.get("timestamp")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


_FETCH_ATTEMPTS = 3
_FETCH_BACKOFF_S = 1.5


def _fetch_bars(ticker: str, start: datetime, end: datetime) -> pd.DataFrame | None:
    """Daily bars over [start, end], with the in-progress session removed.

    Retried with backoff. Scoring a log means one fetch per thesis in quick
    succession, and Yahoo rate-limits that burst; a single failed attempt would
    otherwise be indistinguishable from a thesis whose levels cannot be
    recovered, and would silently mark it unscorable.
    """
    key = (ticker, start.date().isoformat(), end.date().isoformat())
    if key in _bar_cache:
        return _bar_cache[key]

    result = None
    for attempt in range(_FETCH_ATTEMPTS):
        try:
            hist = yf.Ticker(to_yfinance_ticker(ticker)).history(
                start=start.date().isoformat(),
                end=(end + timedelta(days=1)).date().isoformat(),
                auto_adjust=True,
            )
        except Exception as e:
            logger.debug("bar fetch failed for %s (attempt %d): %s", ticker, attempt + 1, e)
            hist = None

        if hist is None or hist.empty:
            # Nothing came back at all: a real failure, worth another attempt.
            if attempt < _FETCH_ATTEMPTS - 1:
                time.sleep(_FETCH_BACKOFF_S * (attempt + 1))
            continue

        # Two filters, both required. drop_incomplete_bars removes the pre-open
        # placeholder (NaN close); drop_unsettled_session removes the live
        # session, whose High/Low are still running extremes. Scoring against
        # those resolves an outcome early and optimistically -- the same bias
        # the same-bar rule exists to prevent.
        #
        # Filtering everything away is a legitimate answer, not a failure: it
        # means no session has settled since the thesis was logged. Return the
        # empty frame rather than None so the caller reports `pending` instead
        # of `no_data`, and do not burn retries on it.
        result = drop_unsettled_session(drop_incomplete_bars(hist))
        break

    _bar_cache[key] = result
    return result


# Populated per scoring/migration pass; several theses share a ticker and a
# window, and refetching each one is what triggers the rate limiting above.
_bar_cache: dict[tuple[str, str, str], pd.DataFrame | None] = {}


def _clear_bar_cache() -> None:
    _bar_cache.clear()


# ── outcome resolution ────────────────────────────────────────────────────────

def resolve_outcome(
    entry: dict, bars: pd.DataFrame, as_of: datetime, fetch_succeeded: bool = True
) -> dict:
    """Score one thesis against its price history.

    ``bars`` must already start on the first session after the thesis was
    logged; this function does not re-apply that offset.

    An empty frame has two very different causes and they must not share a
    label: a thesis logged after the most recent close simply has no sessions
    to be judged on yet (``pending``), whereas a failed fetch means the thesis
    is unscored for a reason on our side (``no_data``). Reporting the first as
    the second makes a healthy young forward test look broken.
    """
    entry_price = float(entry["entry_price"])
    stop = float(entry["stop_loss"])
    target = float(entry["target_price"])
    is_long = entry.get("direction", "long") == "long"

    target_date = datetime.strptime(entry["target_date"], "%Y-%m-%d").replace(
        tzinfo=timezone.utc
    )
    expired = as_of.date() > target_date.date()

    outcome, exit_price, exit_date, ambiguous = None, None, None, False

    for stamp, bar in bars.iterrows():
        high, low = float(bar["High"]), float(bar["Low"])
        hit_target = high >= target if is_long else low <= target
        hit_stop = low <= stop if is_long else high >= stop

        if hit_target and hit_stop:
            # Daily bars cannot order two touches inside one session. Resolving
            # this as a win would let the forward test grade its own homework.
            outcome, exit_price, ambiguous = "hit_stop", stop, True
        elif hit_target:
            outcome, exit_price = "hit_target", target
        elif hit_stop:
            outcome, exit_price = "hit_stop", stop
        else:
            continue

        exit_date = stamp.date().isoformat()
        break

    if outcome is None:
        if bars.empty:
            return {
                "outcome": "pending" if fetch_succeeded else "no_data",
                "note": (
                    "no trading session has closed since this thesis was logged; "
                    "nothing to score yet"
                    if fetch_succeeded
                    else "price history could not be fetched for this ticker"
                ),
            }
        exit_price = float(bars["Close"].iloc[-1])
        exit_date = bars.index[-1].date().isoformat()
        outcome = "expired" if expired else "open"

    move = (exit_price - entry_price) if is_long else (entry_price - exit_price)
    return_pct = move / entry_price * 100.0

    result = {
        "outcome": outcome,
        "exit_price": safe_round(exit_price, 2),
        "exit_date": exit_date,
        "return_pct": safe_round(return_pct, 2),
        "bars_held": int(len(bars)) if outcome in ("open", "expired")
        else int(bars.index.get_loc(pd.Timestamp(exit_date, tz=bars.index.tz)) + 1)
        if exit_date else None,
    }
    if ambiguous:
        result["same_bar_ambiguous"] = True
        result["note"] = (
            "target and stop were both touched in one session; daily bars cannot "
            "order them, so this is scored as a stop"
        )

    pos = entry.get("position_value_idr")
    if pos:
        fees = float(pos) * (DEFAULT_BUY_FEE + DEFAULT_SELL_FEE)
        shares = float(pos) / entry_price
        result["realized_pnl_idr"] = safe_round(shares * move - fees, 0)
        result["fees_idr"] = safe_round(fees, 0)
    return result


def _bars_after_log(bars: pd.DataFrame, logged: datetime) -> pd.DataFrame:
    """Sessions strictly after the thesis was logged."""
    if bars is None or bars.empty:
        return pd.DataFrame()
    cutoff = pd.Timestamp(logged.date())
    index = bars.index
    if index.tz is not None:
        cutoff = cutoff.tz_localize(index.tz)
    return bars[index > cutoff]


# ── aggregation ───────────────────────────────────────────────────────────────

def _summarise(scored: list[dict]) -> dict:
    decided = [s for s in scored if s["outcome"] in ("hit_target", "hit_stop")]
    wins = [s for s in decided if s["outcome"] == "hit_target"]
    returns = [s["return_pct"] for s in scored
               if s["outcome"] in ("hit_target", "hit_stop", "expired")
               and s["return_pct"] is not None]

    realized_win_rate = (len(wins) / len(decided)) if decided else None
    predicted = [s["ai_win_prob"] for s in decided if s.get("ai_win_prob") is not None]
    mean_predicted = (sum(predicted) / len(predicted)) if predicted else None

    pnl = [s["realized_pnl_idr"] for s in scored if s.get("realized_pnl_idr") is not None
           and s["outcome"] != "open"]

    summary = {
        "logged": len(scored),
        "decided": len(decided),
        "hit_target": len(wins),
        "hit_stop": len(decided) - len(wins),
        "expired": sum(1 for s in scored if s["outcome"] == "expired"),
        "open": sum(1 for s in scored if s["outcome"] == "open"),
        "pending": sum(1 for s in scored if s["outcome"] == "pending"),
        "unscorable": sum(1 for s in scored if s["outcome"] in ("no_data", "no_levels")),
        "realized_win_rate": safe_round(realized_win_rate, 4),
        "mean_predicted_win_prob": safe_round(mean_predicted, 4),
        "avg_return_pct": safe_round(sum(returns) / len(returns), 2) if returns else None,
        "total_realized_pnl_idr": safe_round(sum(pnl), 0) if pnl else None,
        "same_bar_ambiguous": sum(1 for s in scored if s.get("same_bar_ambiguous")),
    }
    if realized_win_rate is not None and mean_predicted is not None:
        # Positive means the theses were optimistic about themselves.
        summary["calibration_gap"] = safe_round(mean_predicted - realized_win_rate, 4)
    return summary


def _by_strategy(scored: list[dict]) -> dict:
    groups: dict[str, list[dict]] = {}
    for s in scored:
        groups.setdefault(s.get("strategy") or "unknown", []).append(s)
    return {name: _summarise(rows) for name, rows in sorted(groups.items())}


# ── the tool ──────────────────────────────────────────────────────────────────

def _score_all(strategy: str | None, include_open: bool) -> dict:
    _clear_bar_cache()
    log_file = predictions_log_file()
    logs = _read_predictions_log(log_file)
    if not logs:
        return {
            "error": True,
            "error_type": "no_predictions",
            "message": f"No predictions logged yet at {log_file}.",
            "suggestion": "Log a thesis with evaluate_and_log_thesis first.",
        }

    as_of = datetime.now(timezone.utc)
    scored: list[dict] = []

    for raw in logs:
        if strategy and strategy.lower() not in (raw.get("strategy") or "").lower():
            continue

        base = {
            "ticker": raw.get("ticker"),
            "strategy": raw.get("strategy"),
            "logged_at": raw.get("timestamp"),
            "target_date": raw.get("target_date"),
            "ai_win_prob": raw.get("ai_win_prob"),
            "initial_ev_idr": raw.get("initial_ev"),
            "levels_source": raw.get("levels_source"),
        }

        entry = dict(raw)
        logged = _logged_date(entry)
        if logged is None or not entry.get("ticker") or not entry.get("target_date"):
            scored.append({**base, "outcome": "no_levels",
                           "note": "missing timestamp, ticker or target_date"})
            continue

        # Recover levels for entries written before the schema carried them.
        if entry.get("entry_price") is None:
            recovered = _levels_from_reasoning(entry)
            source = "parsed_from_reasoning"
            bars_probe = None
            if recovered is None:
                bars_probe = _fetch_bars(entry["ticker"], logged - timedelta(days=10), logged)
                if bars_probe is not None and not bars_probe.empty:
                    recovered = _levels_from_ratios(entry, float(bars_probe["Close"].iloc[-1]))
                    source = "reconstructed_from_ratios"
            if recovered is None:
                scored.append({**base, "outcome": "no_levels",
                               "note": "pre-v3 entry with no recoverable entry/stop/target"})
                continue
            entry["entry_price"], entry["stop_loss"], entry["target_price"] = recovered
            entry.setdefault("direction", "long")
            base["levels_source"] = source

        target_dt = datetime.strptime(entry["target_date"], "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
        window_end = min(as_of, target_dt) if target_dt > as_of else target_dt
        bars = _fetch_bars(entry["ticker"], logged, max(window_end, as_of))
        window = _bars_after_log(bars, logged) if bars is not None else pd.DataFrame()

        # Never look past the horizon the thesis committed to.
        if not window.empty:
            horizon = pd.Timestamp(target_dt.date())
            if window.index.tz is not None:
                horizon = horizon.tz_localize(window.index.tz)
            window = window[window.index <= horizon]

        outcome = resolve_outcome(entry, window, as_of, fetch_succeeded=bars is not None)
        row = {
            **base,
            "entry_price": entry["entry_price"],
            "stop_loss": safe_round(entry["stop_loss"], 2),
            "target_price": safe_round(entry["target_price"], 2),
            "direction": entry.get("direction", "long"),
            **outcome,
        }
        row.setdefault("levels_source", "declared")
        scored.append(row)

    visible = scored if include_open else [s for s in scored if s["outcome"] not in ("open", "pending")]
    return {
        "evaluated_at": as_of.isoformat(),
        "log_file": str(log_file),
        "filter_strategy": strategy,
        "summary": _summarise(scored),
        "by_strategy": _by_strategy(scored),
        "predictions": visible,
        "interpretation": (
            "realized_win_rate counts only theses that reached their target or stop. "
            "calibration_gap is mean_predicted_win_prob minus realized_win_rate: "
            "positive means the logged theses were optimistic. Entries whose "
            "levels_source is not 'declared' were reconstructed from a pre-v3 record "
            "and are weaker evidence than ones logged with explicit levels."
        ),
        "disclaimer": (
            "This output is for educational and analytical purposes only. "
            "Not financial advice. Trading decisions remain the user's full responsibility."
        ),
    }


async def evaluate_predictions(strategy: str | None = None, include_open: bool = True) -> dict:
    """Score every logged thesis against the price history that followed it."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_score_all, strategy, bool(include_open)),
            timeout=_T_EVALUATE,
        )
    except asyncio.TimeoutError:
        return {
            "error": True,
            "error_type": "timeout",
            "message": f"Prediction evaluation exceeded {_T_EVALUATE:.0f}s.",
            "suggestion": "Retry — each thesis needs its own price history fetch.",
        }
    except Exception as e:
        logger.exception("prediction evaluation failed")
        return {"error": True, "error_type": "evaluation_failed", "message": str(e)}


# ── migration ─────────────────────────────────────────────────────────────────

def _migrate_entries(logs: list[dict]) -> tuple[list[dict], dict]:
    """Upgrade pre-v3 records in place, recovering levels where possible."""
    stats = {"already_v3": 0, "parsed_from_reasoning": 0,
             "reconstructed_from_ratios": 0, "unresolvable": 0}
    out = []

    for raw in logs:
        entry = dict(raw)
        if entry.get("entry_price") is not None:
            entry.setdefault("levels_source", "declared")
            entry.setdefault("direction", "long")
            entry["schema_version"] = SCHEMA_VERSION
            stats["already_v3"] += 1
            out.append(entry)
            continue

        recovered = _levels_from_reasoning(entry)
        source = "parsed_from_reasoning"

        if recovered is None:
            logged = _logged_date(entry)
            if logged and entry.get("ticker"):
                bars = _fetch_bars(entry["ticker"], logged - timedelta(days=10), logged)
                if bars is not None and not bars.empty:
                    recovered = _levels_from_ratios(entry, float(bars["Close"].iloc[-1]))
                    source = "reconstructed_from_ratios"

        if recovered is None:
            entry["levels_source"] = "unresolvable"
            stats["unresolvable"] += 1
        else:
            entry["entry_price"], entry["stop_loss"], entry["target_price"] = recovered
            entry["direction"] = entry.get("direction", "long")
            entry["levels_source"] = source
            stats[source] += 1

        entry["schema_version"] = SCHEMA_VERSION
        out.append(entry)

    return out, stats


def migrate_predictions_log() -> dict:
    """Rewrite the log at schema 3, backing the old file up first."""
    _clear_bar_cache()
    log_file = predictions_log_file()
    with _log_lock, _log_file_lock(log_file):
        logs = _read_predictions_log(log_file)
        if not logs:
            return {"migrated": 0, "message": "log is empty; nothing to migrate"}

        backup = log_file.with_suffix(
            f".v2-backup-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
        )
        _atomic_write_json(backup, logs)

        migrated, stats = _migrate_entries(logs)
        _atomic_write_json(log_file, migrated)

    return {
        "migrated": len(migrated),
        "backup": str(backup),
        "schema_version": SCHEMA_VERSION,
        "levels_recovery": stats,
    }
