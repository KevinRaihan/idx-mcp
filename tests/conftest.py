"""Shared fixtures.

Tests are split in two groups:

* unit tests, which build DataFrames by hand and never touch the network;
* tests marked ``network``, which hit the real upstream providers.

Run the fast set with ``pytest -m "not network"``.
"""

import os
import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Point every server write at a throwaway directory, never the real ~/.idx-mcp."""
    monkeypatch.setenv("IDX_MCP_HOME", str(tmp_path / "idx-mcp-home"))
    yield


@pytest.fixture(autouse=True)
def clear_cache():
    """The TTL cache is module-global; a stale entry would mask the next assertion."""
    from src.utils.cache import cache

    cache.clear()
    yield
    cache.clear()


def make_ohlcv(
    rows: int = 260,
    start_close: float = 1000.0,
    drift: float = 0.0,
    volume: int = 5_000_000,
    spread_pct: float = 1.0,
) -> pd.DataFrame:
    """Build a deterministic OHLCV frame shaped like a yfinance download."""
    dates = pd.bdate_range(end="2026-08-26", periods=rows)
    closes = [start_close + drift * i for i in range(rows)]
    half = spread_pct / 200.0
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c * (1 + half) for c in closes],
            "Low": [c * (1 - half) for c in closes],
            "Close": closes,
            "Volume": [volume] * rows,
        },
        index=dates,
    )


@pytest.fixture
def ohlcv_factory():
    return make_ohlcv
