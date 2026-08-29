"""Shared OHLCV cache for the market-wide scanners.

Every scanner used to run its own ``_download_batch`` over the full BEI
universe. With five scanners that was five downloads of ~178 tickers; at ten it
would have been ten, and an agent running the full ensemble paid the network
cost of the whole universe once per strategy.

The scan *results* were cached, but the download underneath them was not, so a
cold cache meant N full fetches of the same bars.

This module fetches the universe once, at the longest period any scanner needs,
and hands every scanner a slice of it. One fetch serves all ten. It also makes
cross-scan confluence cheap: every strategy is looking at the identical frames,
so the same close on the same date cannot disagree between two scans.

Slices are copies. Scanners add indicator columns to what they receive, and a
shared frame that accumulated ``bb_width`` from one scan and ``rsi`` from
another would leak state between strategies in a way that only shows up under
specific call orderings.
"""

import logging
import threading

import pandas as pd

from ..utils.cache import cache
from .scanner import _download_batch, _load_tickers, _to_jk

logger = logging.getLogger("idx-mcp.tools.universe")

# Fetched once; every scanner slices its own window out of this.
BASE_PERIOD = "2y"
BATCH_SIZE = 80
TTL_UNIVERSE = 14_400  # 4 h, matching the scan-result TTL

# Approximate trading days per period string. IDX trades ~245 days a year;
# the tail is deliberately generous so a 200-period SMA still has its full
# lookback at the left edge of a "1y" slice.
_PERIOD_BARS = {
    "1mo": 22,
    "2mo": 44,
    "3mo": 63,
    "6mo": 126,
    "1y": 252,
    "2y": 504,
    "5y": 1260,
    "max": 10_000,
}

_lock = threading.Lock()


def _fetch_base() -> dict[str, pd.DataFrame]:
    """Download the whole universe at ``BASE_PERIOD``, in batches."""
    jk_list = [_to_jk(t) for t in _load_tickers()]
    data: dict[str, pd.DataFrame] = {}
    for i in range(0, len(jk_list), BATCH_SIZE):
        # min_rows=1 here: the base fetch keeps everything it can get and lets
        # each caller apply its own depth requirement against its own slice.
        data.update(_download_batch(jk_list[i : i + BATCH_SIZE], period=BASE_PERIOD, min_rows=1))
    logger.info("universe fetch: %d/%d tickers resolved at %s", len(data), len(jk_list), BASE_PERIOD)
    return data


def _base_universe(force_refresh: bool = False) -> dict[str, pd.DataFrame]:
    """The cached base frames, fetching them if the cache is cold.

    The lock makes a cold cache cost one download rather than one per
    concurrent scanner: ``asyncio.to_thread`` means several scans can enter
    this function at the same moment.
    """
    if not force_refresh:
        cached = cache.get("universe_ohlcv", BASE_PERIOD)
        if cached is not None:
            return cached

    with _lock:
        if not force_refresh:
            # Another thread may have populated it while we waited for the lock.
            cached = cache.get("universe_ohlcv", BASE_PERIOD)
            if cached is not None:
                return cached
        data = _fetch_base()
        # An empty result means upstream is down. Caching that for four hours
        # would turn a transient outage into an afternoon of empty scans.
        if data:
            cache.set("universe_ohlcv", BASE_PERIOD, data, TTL_UNIVERSE)
        return data


def load_universe(
    period: str = "1y", min_rows: int = 20, force_refresh: bool = False
) -> dict[str, pd.DataFrame]:
    """Return ``{ticker: OHLCV}`` for the BEI universe over ``period``.

    Drop-in replacement for the per-scanner ``_download_batch`` loop. Tickers
    with fewer than ``min_rows`` bars in the sliced window are omitted, so a
    scanner needing 150 bars still gets a clean dict it can iterate.
    """
    base = _base_universe(force_refresh=force_refresh)
    bars = _PERIOD_BARS.get(period, _PERIOD_BARS["1y"])

    sliced: dict[str, pd.DataFrame] = {}
    for ticker, df in base.items():
        window = df.tail(bars)
        if len(window) >= min_rows:
            sliced[ticker] = window.copy()
    return sliced


def universe_size() -> int:
    """Universe count, for scan envelopes that report coverage."""
    return len(_load_tickers())


def invalidate_universe() -> None:
    """Drop the cached base frames. Used by tests and forced rescans."""
    with _lock:
        # A negative TTL puts expires_at in the past so the next get() evicts.
        # TTLCache has no delete, and ttl=0 would leave the entry alive for the
        # remainder of the current clock tick.
        cache.set("universe_ohlcv", BASE_PERIOD, None, -1)
