"""Regression tests for the OHLCV download chokepoint.

Every scanner tool reads its signal off ``df.iloc[-1]`` of a frame produced by
``_download_batch``. Yahoo appends the live session as a row that carries a
Volume figure but all-NaN OHLC; ``dropna(how="all")`` kept it, so the last row
had a NaN close, every filter compared against NaN, and the scans reported zero
signals market-wide. These tests pin the cleaning behaviour.
"""

import numpy as np
import pandas as pd
import pytest

from src.tools import scanner


def _multiindex_frame(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    return pd.concat(frames, axis=1)


@pytest.fixture
def patched_download(monkeypatch):
    """Swap yf.download for a canned frame so the test is deterministic."""

    def install(raw: pd.DataFrame):
        monkeypatch.setattr(scanner.yf, "download", lambda *a, **k: raw)

    return install


def _base_frame(rows=40, volume=5_000_000):
    idx = pd.bdate_range(end="2026-08-26", periods=rows)
    closes = np.linspace(1000, 1100, rows)
    return pd.DataFrame(
        {"Open": closes, "High": closes * 1.01, "Low": closes * 0.99,
         "Close": closes, "Volume": [volume] * rows},
        index=idx,
    )


def test_partial_live_bar_is_dropped(patched_download):
    df = _base_frame()
    # The live session: volume ticking, OHLC not yet published.
    df.loc[pd.Timestamp("2026-08-27")] = [np.nan, np.nan, np.nan, np.nan, 1_234_500]

    patched_download(_multiindex_frame({"AALI.JK": df}))
    out = scanner._download_batch(["AALI.JK"], period="3mo")

    assert "AALI" in out
    result = out["AALI"]
    assert not result["Close"].isna().any()
    assert result.index[-1] == pd.Timestamp("2026-08-26")
    assert len(result) == 40


def test_last_row_is_usable_for_signal_evaluation(patched_download):
    """The concrete failure: iloc[-1] must expose a real close, not NaN."""
    df = _base_frame()
    df.loc[pd.Timestamp("2026-08-27")] = [np.nan, np.nan, np.nan, np.nan, 999]
    patched_download(_multiindex_frame({"BBCA.JK": df}))

    row = scanner._download_batch(["BBCA.JK"], period="3mo")["BBCA"].iloc[-1]
    assert scanner._f(row["Close"]) is not None
    assert scanner._f(row["Volume"]) is not None


def test_interior_nan_close_rows_are_removed(patched_download):
    df = _base_frame()
    df.iloc[10, df.columns.get_loc("Close")] = np.nan
    patched_download(_multiindex_frame({"TLKM.JK": df}))

    out = scanner._download_batch(["TLKM.JK"], period="3mo")["TLKM"]
    assert len(out) == 39
    assert not out["Close"].isna().any()


def test_fully_empty_rows_still_dropped(patched_download):
    df = _base_frame()
    df.loc[pd.Timestamp("2026-08-27")] = [np.nan] * 5
    patched_download(_multiindex_frame({"ASII.JK": df}))
    assert len(scanner._download_batch(["ASII.JK"], period="3mo")["ASII"]) == 40


def test_min_rows_threshold_is_honoured(patched_download):
    short = _base_frame(rows=10)
    patched_download(_multiindex_frame({"GOTO.JK": short}))

    assert scanner._download_batch(["GOTO.JK"], period="1mo", min_rows=20) == {}
    assert "GOTO" in scanner._download_batch(["GOTO.JK"], period="1mo", min_rows=5)


def test_min_rows_counted_after_cleaning(patched_download):
    """A frame padded to length by unusable bars must not slip past min_rows."""
    df = _base_frame(rows=20)
    for i, day in enumerate(pd.bdate_range("2026-08-27", periods=5)):
        df.loc[day] = [np.nan, np.nan, np.nan, np.nan, 100 + i]
    patched_download(_multiindex_frame({"BREN.JK": df}))

    assert scanner._download_batch(["BREN.JK"], period="3mo", min_rows=25) == {}


def test_missing_ticker_is_skipped_not_fatal(patched_download):
    good = _base_frame()
    patched_download(_multiindex_frame({"AALI.JK": good}))

    out = scanner._download_batch(["AALI.JK", "DELISTED.JK"], period="3mo")
    assert set(out) == {"AALI"}


def test_empty_input_returns_empty():
    assert scanner._download_batch([], period="1y") == {}


def test_download_exception_is_contained(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("upstream refused the connection")

    monkeypatch.setattr(scanner.yf, "download", boom)
    assert scanner._download_batch(["AALI.JK"], period="1y") == {}
