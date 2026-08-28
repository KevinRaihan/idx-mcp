"""Shared plumbing for the v1.1 strategy scanners.

The MA Ketat and Golden Cross scanners already return a rich envelope
(counts, timestamp, disclaimer). These helpers give the mean-reversion,
volatility-squeeze and volume-accumulation scanners the same shape so every
scanner is interchangeable from the agent's point of view.
"""

import logging
import time

from ..utils.time_utils import format_wib_iso, now_wib

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
) -> dict:
    """Assemble the standard scan response.

    ``top_10`` is kept as the primary key for backwards compatibility with the
    existing skill templates; ``signals`` is its alias.
    """
    top = signals[:top_n]
    return {
        "strategy": strategy,
        "scan_time_wib": format_wib_iso(now_wib()),
        "elapsed_seconds": round(elapsed_s, 1),
        "universe_size": total_scanned,
        "tickers_with_data": downloaded,
        "tickers_without_data": failed,
        "signals_found": len(signals),
        "filters_applied": filters,
        "top_10": top,
        "signals": top,
        "disclaimer": DISCLAIMER,
    }


def scan_timer():
    """Monotonic start marker for ``build_envelope(elapsed_s=...)``."""
    return time.monotonic()


def elapsed_since(start: float) -> float:
    return time.monotonic() - start


def log_ticker_failure(logger: logging.Logger, ticker: str, exc: Exception) -> None:
    """Per-ticker failures are expected (thin data, halts); keep them at debug."""
    logger.debug("skipping %s: %s: %s", ticker, type(exc).__name__, exc)
