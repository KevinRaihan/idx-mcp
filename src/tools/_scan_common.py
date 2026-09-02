"""Shared plumbing for the v1.1 strategy scanners.

The MA Ketat and Golden Cross scanners already return a rich envelope
(counts, timestamp, disclaimer). These helpers give the mean-reversion,
volatility-squeeze and volume-accumulation scanners the same shape so every
scanner is interchangeable from the agent's point of view.
"""

import logging
import time

from ..utils.time_utils import format_wib_iso, now_wib


class Funnel:
    """Survivor counts per filter stage.

    Zero signals is an ambiguous result: a quiet market and a broken filter look
    identical from outside a scan. That ambiguity is exactly how
    ``scan_volume_accumulation`` stayed silently dead — its MIN_ROWS exceeded the
    bars its own SCAN_PERIOD fetched, so it could never return anything, and the
    empty output was indistinguishable from a calm day.

    Stages are declared up front and counted in order, so a stage reading zero
    while the one above it reads 130 names the filter that emptied the room.
    """

    __slots__ = ("_counts",)

    def __init__(self, *stages: str):
        self._counts: dict[str, int] = {s: 0 for s in stages}

    def passed(self, stage: str) -> None:
        # A typo'd stage name would otherwise vanish into a silent no-op and
        # report a funnel that quietly disagrees with the code.
        self._counts[stage] += 1

    def to_dict(self) -> dict[str, int]:
        return dict(self._counts)

#: What confidence_score actually is, emitted on every scan envelope.
SCORE_BASIS = 'heuristic_ranking_not_probability'
SCORE_NOTE = (
    "confidence_score ranks candidates within this scan only. It is a "
    "hand-weighted heuristic, not a calibrated probability and not comparable "
    "across strategies. Use run_backtest for a base rate."
)

DISCLAIMER = (
    "This output is for educational and analytical purposes only. "
    "Not financial advice. Trading decisions remain the user's full responsibility."
)


def build_envelope(
    *,
    strategy: str,
    signals: list[dict],
    total_scanned: int,
    downloaded: int,
    failed: int,
    filters: dict,
    elapsed_s: float,
    top_n: int = 10,
    funnel: dict | None = None,
) -> dict:
    """Assemble the standard scan response.

    ``top_10`` is the ranked head of the list. The ``signals`` alias it used to
    carry is gone: a key named ``signals`` sitting beside ``signals_found: 134``
    but holding 10 rows is a trap, and it caused a real misread -- a ticker was
    reported as unflagged by a risk scan when it had simply been truncated away.
    Use ``all_signals`` for the complete set.

    ``all_signals`` carries every signal, matching ``signals_found``. Without it
    a scan reporting 134 flags handed back only 10 rows under a key named
    ``signals``, so a risk scan could not be used to check whether a specific
    ticker was flagged — the answer was silently truncated away. The legacy
    MA Ketat and Golden Cross scanners already returned ``all_signals``; this
    brings the rest into line with that contract.

    ``funnel`` is an optional per-stage survivor count. See ``Funnel``.
    """
    top = signals[:top_n]
    envelope = {
        "strategy": strategy,
        "scan_time_wib": format_wib_iso(now_wib()),
        "elapsed_seconds": round(elapsed_s, 1),
        "universe_size": total_scanned,
        "tickers_with_data": downloaded,
        "tickers_without_data": failed,
        "signals_found": len(signals),
        "filters_applied": filters,
        # confidence_score is a hand-weighted ranking, and a bare number called
        # "confidence" reads as a probability. Saying what it is not is cheaper
        # than someone position-sizing off it.
        "score_basis": SCORE_BASIS,
        "score_note": SCORE_NOTE,
        "top_10": top,
        "all_signals": signals,
        "disclaimer": DISCLAIMER,
    }
    if funnel is not None:
        envelope["filter_funnel"] = funnel.to_dict() if isinstance(funnel, Funnel) else funnel
    return envelope


def scan_timer():
    """Monotonic start marker for ``build_envelope(elapsed_s=...)``."""
    return time.monotonic()


def elapsed_since(start: float) -> float:
    return time.monotonic() - start


def log_ticker_failure(logger: logging.Logger, ticker: str, exc: Exception) -> None:
    """Per-ticker failures are expected (thin data, halts); keep them at debug."""
    logger.debug("skipping %s: %s: %s", ticker, type(exc).__name__, exc)
