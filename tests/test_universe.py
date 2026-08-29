"""The shared universe cache.

The claim this module has to earn: ten scanners cost one download, not ten.
These tests count actual calls into ``_download_batch`` rather than trusting
that the cache is wired up.
"""

import threading

import pandas as pd
import pytest

from src.tools import universe as uni
from src.utils.cache import cache

from conftest import make_ohlcv


@pytest.fixture
def counting_download(monkeypatch):
    """Replace the network layer with a counter over synthetic frames."""
    calls = []

    def fake(jk_tickers, period="1y", min_rows=20):
        calls.append({"tickers": list(jk_tickers), "period": period, "min_rows": min_rows})
        return {t.replace(".JK", ""): make_ohlcv(rows=520) for t in jk_tickers}

    monkeypatch.setattr(uni, "_download_batch", fake)
    monkeypatch.setattr(uni, "_load_tickers", lambda: ["AAAA", "BBBB", "CCCC"])
    return calls


def test_first_load_fetches_and_second_load_does_not(counting_download):
    first = uni.load_universe(period="1y")
    batches_after_first = len(counting_download)
    assert batches_after_first > 0
    assert set(first) == {"AAAA", "BBBB", "CCCC"}

    uni.load_universe(period="1y")
    assert len(counting_download) == batches_after_first, "second load hit the network"


def test_ten_scans_at_five_periods_still_cost_one_fetch(counting_download):
    """The whole point: differing scan periods must not each trigger a download."""
    for period in ["3mo", "6mo", "1y", "2y", "1y", "6mo", "3mo", "2y", "1y", "6mo"]:
        uni.load_universe(period=period)

    fetched_periods = {c["period"] for c in counting_download}
    assert fetched_periods == {uni.BASE_PERIOD}, "a scan period leaked into the fetch"
    # Three tickers fit in one batch, so exactly one call.
    assert len(counting_download) == 1


def test_period_slices_the_base_frame(counting_download):
    short = uni.load_universe(period="3mo")
    long = uni.load_universe(period="2y")

    assert len(short["AAAA"]) == uni._PERIOD_BARS["3mo"]
    assert len(long["AAAA"]) > len(short["AAAA"])
    # The short window is the tail of the long one, not the head.
    assert short["AAAA"].index[-1] == long["AAAA"].index[-1]


def test_unknown_period_falls_back_to_one_year(counting_download):
    assert len(uni.load_universe(period="banana")["AAAA"]) == uni._PERIOD_BARS["1y"]


def test_min_rows_filters_shallow_tickers(monkeypatch):
    def fake(jk_tickers, period="1y", min_rows=20):
        return {"DEEP": make_ohlcv(rows=400), "THIN": make_ohlcv(rows=30)}

    monkeypatch.setattr(uni, "_download_batch", fake)
    monkeypatch.setattr(uni, "_load_tickers", lambda: ["DEEP", "THIN"])

    assert set(uni.load_universe(period="1y", min_rows=20)) == {"DEEP", "THIN"}
    assert set(uni.load_universe(period="1y", min_rows=100)) == {"DEEP"}


def test_slices_are_copies_so_scanners_cannot_leak_columns(counting_download):
    """Two scanners enriching the same ticker must not see each other's columns."""
    first = uni.load_universe(period="1y")
    first["AAAA"]["bb_width"] = 1.23

    second = uni.load_universe(period="1y")
    assert "bb_width" not in second["AAAA"].columns


def test_an_empty_fetch_is_not_cached(monkeypatch):
    """Caching an outage for four hours would turn a blip into a dead afternoon."""
    calls = []

    def fake(jk_tickers, period="1y", min_rows=20):
        calls.append(1)
        return {}

    monkeypatch.setattr(uni, "_download_batch", fake)
    monkeypatch.setattr(uni, "_load_tickers", lambda: ["AAAA"])

    assert uni.load_universe() == {}
    assert uni.load_universe() == {}
    assert len(calls) == 2, "an empty result was cached"


def test_force_refresh_bypasses_the_cache(counting_download):
    uni.load_universe(period="1y")
    n = len(counting_download)
    uni.load_universe(period="1y", force_refresh=True)
    assert len(counting_download) > n


def test_invalidate_universe_forces_the_next_load_to_fetch(counting_download):
    uni.load_universe(period="1y")
    n = len(counting_download)
    uni.invalidate_universe()
    uni.load_universe(period="1y")
    assert len(counting_download) > n


def test_concurrent_cold_loads_fetch_once(monkeypatch):
    """asyncio.to_thread lets several scans enter a cold cache simultaneously."""
    calls = []
    barrier = threading.Barrier(4, timeout=10)

    def fake(jk_tickers, period="1y", min_rows=20):
        calls.append(1)
        return {t.replace(".JK", ""): make_ohlcv(rows=300) for t in jk_tickers}

    monkeypatch.setattr(uni, "_download_batch", fake)
    monkeypatch.setattr(uni, "_load_tickers", lambda: ["AAAA", "BBBB"])

    results = []

    def worker():
        barrier.wait()   # release all four into load_universe together
        results.append(uni.load_universe(period="1y"))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert len(results) == 4
    assert len(calls) == 1, f"cold cache caused {len(calls)} downloads, expected 1"


def test_batching_splits_large_universes(monkeypatch):
    calls = []

    def fake(jk_tickers, period="1y", min_rows=20):
        calls.append(len(jk_tickers))
        return {t.replace(".JK", ""): make_ohlcv(rows=300) for t in jk_tickers}

    monkeypatch.setattr(uni, "_download_batch", fake)
    monkeypatch.setattr(uni, "_load_tickers", lambda: [f"T{i:03d}" for i in range(178)])

    data = uni.load_universe(period="1y")
    assert len(data) == 178
    assert max(calls) <= uni.BATCH_SIZE
    assert sum(calls) == 178


def test_universe_size_reports_the_ticker_list(monkeypatch):
    monkeypatch.setattr(uni, "_load_tickers", lambda: ["A", "B", "C"])
    assert uni.universe_size() == 3


def test_base_fetch_keeps_shallow_tickers_for_callers_to_filter(counting_download):
    """min_rows=1 on the base fetch: depth is each scanner's decision, not the cache's."""
    uni.load_universe(period="1y")
    assert all(c["min_rows"] == 1 for c in counting_download)
