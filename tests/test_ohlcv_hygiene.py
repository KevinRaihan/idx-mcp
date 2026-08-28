"""The shared incomplete-bar filter.

Applied at every point where price history enters the system: the scanner batch
download, get_technicals, and get_trade_setup.
"""

import numpy as np
import pandas as pd
import pytest

from src.utils.ohlcv import drop_incomplete_bars


def _frame(rows=30):
    idx = pd.bdate_range(end="2026-08-26", periods=rows)
    closes = np.linspace(1000, 1100, rows)
    return pd.DataFrame(
        {"Open": closes, "High": closes * 1.01, "Low": closes * 0.99,
         "Close": closes, "Volume": [1_000_000] * rows},
        index=idx,
    )


def test_yahoo_live_session_bar_is_removed():
    df = _frame()
    df.loc[pd.Timestamp("2026-08-27")] = [np.nan, np.nan, np.nan, np.nan, 500_000]
    out = drop_incomplete_bars(df)

    assert len(out) == 30
    assert out.index[-1] == pd.Timestamp("2026-08-26")


def test_complete_frames_pass_through_unchanged():
    df = _frame()
    pd.testing.assert_frame_equal(drop_incomplete_bars(df), df)


def test_rolling_indicators_survive_the_filter():
    """A trailing NaN close makes rolling min/max NaN, which nulled the stochastic."""
    df = _frame()
    df.loc[pd.Timestamp("2026-08-27")] = [np.nan, np.nan, np.nan, np.nan, 500_000]

    dirty_k = df["Close"].rolling(14).min().iloc[-1]
    assert np.isnan(dirty_k)

    clean = drop_incomplete_bars(df)
    assert not np.isnan(clean["Close"].rolling(14).min().iloc[-1])


def test_spot_price_is_not_nan_after_cleaning():
    df = _frame()
    df.loc[pd.Timestamp("2026-08-27")] = [np.nan, np.nan, np.nan, np.nan, 500_000]
    assert np.isnan(float(df["Close"].iloc[-1]))
    assert float(drop_incomplete_bars(df)["Close"].iloc[-1]) == pytest.approx(1100.0)


def test_multiple_trailing_partial_bars_are_all_removed():
    df = _frame()
    for day in pd.bdate_range("2026-08-27", periods=3):
        df.loc[day] = [np.nan, np.nan, np.nan, np.nan, 1_000]
    assert len(drop_incomplete_bars(df)) == 30


def test_interior_gaps_are_removed_too():
    df = _frame()
    df.iloc[5, df.columns.get_loc("Close")] = np.nan
    assert len(drop_incomplete_bars(df)) == 29


def test_empty_and_missing_column_inputs_are_safe():
    empty = pd.DataFrame()
    assert drop_incomplete_bars(empty).empty
    assert drop_incomplete_bars(None) is None

    no_close = pd.DataFrame({"Volume": [1, 2, 3]})
    pd.testing.assert_frame_equal(drop_incomplete_bars(no_close), no_close)


def test_alternative_price_column_is_honoured():
    df = pd.DataFrame({"Adj Close": [1.0, np.nan, 3.0], "Volume": [1, 2, 3]})
    assert len(drop_incomplete_bars(df, price_col="Adj Close")) == 2
