"""Signal logic for the five v1.2 strategy scanners.

Frames are synthesised so every filter can be driven across its boundary
independently of what the market did today. Where a scanner needs a realistic
shape — a pullback that oscillates rather than falling in a straight line — the
builder produces that shape, because a monotonic decline pins RSI into the teens
and would test a condition no real pullback ever presents.
"""

import numpy as np
import pandas as pd
import pytest

from src.tools import breakout_high as bh
from src.tools import distribution as dist
from src.tools import gap as gp
from src.tools import relative_strength as rs
from src.tools import trend_pullback as tp


def _frame(closes, volumes=None, highs=None, lows=None, opens=None, spread_pct=1.2):
    n = len(closes)
    c = np.asarray(closes, dtype=float)
    half = spread_pct / 200.0
    idx = pd.bdate_range(end="2026-08-26", periods=n)
    return pd.DataFrame(
        {
            "Open": opens if opens is not None else c,
            "High": highs if highs is not None else c * (1 + half),
            "Low": lows if lows is not None else c * (1 - half),
            "Close": c,
            "Volume": volumes if volumes is not None else [5_000_000] * n,
        },
        index=idx,
    )


# ── trend pullback ────────────────────────────────────────────────────────────

def uptrend_pullback(rows=280, rise_to=1400.0, dip_bars=18, dip_pct=4.0, chop=1.2):
    """A rising market that pulls back on oscillating bars, not in a straight line."""
    trend_bars = rows - dip_bars
    trend = np.linspace(1000.0, rise_to, trend_bars)
    trend = trend + np.sin(np.arange(trend_bars) * 0.7) * (trend * 0.012)
    dip = np.linspace(rise_to, rise_to * (1 - dip_pct / 100), dip_bars + 1)[1:]
    dip = dip + np.sin(np.arange(dip_bars) * 1.9) * (dip * chop / 100)
    return _frame(np.concatenate([trend, dip]))


def test_trend_pullback_fires_on_a_dip_inside_an_uptrend():
    sig = tp._build_signal("TEST", tp._enrich_df(uptrend_pullback()), 100_000, 40.0, 58.0, 15.0)
    assert sig is not None
    assert sig["structure_intact"] is True
    assert sig["close"] < sig["sma20"]
    assert sig["sma50"] > sig["sma200"]
    assert 40.0 <= sig["rsi"] <= 58.0
    assert 2.0 <= sig["pullback_from_high_pct"] <= 15.0


def test_trend_pullback_rejects_a_downtrend_however_oversold():
    """The distinction from scan_mean_reversion: no trend, no signal."""
    falling = _frame(np.linspace(2000, 1000, 280))
    assert tp._build_signal("TEST", tp._enrich_df(falling), 100_000, 40.0, 58.0, 15.0) is None


def test_trend_pullback_rejects_price_still_above_sma20():
    """Not yet pulled back — an extended stock is not a dip buy."""
    df = tp._enrich_df(uptrend_pullback(dip_bars=2, dip_pct=0.2))
    row = df.iloc[-1]
    assert row["Close"] > row["SMA20"]
    assert tp._build_signal("TEST", df, 100_000, 40.0, 58.0, 15.0) is None


def test_trend_pullback_rejects_a_pullback_that_broke_structure():
    """A 20-day low beneath the 60-day low is a downtrend, not a dip."""
    df = tp._enrich_df(uptrend_pullback(dip_bars=40, dip_pct=22.0))
    row = df.iloc[-1]
    assert row["low_20d"] <= row["low_60d"]
    assert tp._build_signal("TEST", df, 100_000, 40.0, 58.0, 15.0) is None


def test_trend_pullback_respects_the_volume_floor():
    df = tp._enrich_df(uptrend_pullback())
    assert tp._build_signal("TEST", df, 100_000, 40.0, 58.0, 15.0) is not None
    assert tp._build_signal("TEST", df, 50_000_000, 40.0, 58.0, 15.0) is None


def test_trend_pullback_rsi_band_is_a_real_filter():
    df = tp._enrich_df(uptrend_pullback())
    rsi = df.iloc[-1]["rsi"]
    assert tp._build_signal("TEST", df, 100_000, rsi + 1, 90.0, 15.0) is None
    assert tp._build_signal("TEST", df, 100_000, 0.0, rsi - 1, 15.0) is None


async def test_trend_pullback_rejects_an_inverted_rsi_band():
    r = await tp.scan_trend_pullback(rsi_min=60.0, rsi_max=40.0)
    assert r["error"] is True


# ── breakout to new high ──────────────────────────────────────────────────────

def base_then_breakout(rows=150, base=1000.0, base_amp=0.02, breakout_close=1060.0,
                       breakout_vol=3_000_000, base_vol=1_000_000):
    closes = base + np.sin(np.arange(rows - 1) * 0.55) * (base * base_amp)
    closes = np.append(closes, breakout_close)
    vols = [base_vol] * (rows - 1) + [breakout_vol]
    return _frame(closes, volumes=vols)


def test_breakout_fires_above_a_tight_base_on_volume():
    df = bh._enrich_df(base_then_breakout(), 60)
    sig = bh._build_signal("TEST", df, 100_000, 1.5, 25.0)
    assert sig is not None
    assert sig["close"] >= sig["prior_high"]
    assert sig["volume_ratio"] >= 1.5
    assert sig["base_range_pct"] <= 25.0


def test_breakout_requires_clearing_the_prior_high():
    """The prior high excludes today, so the test is not self-satisfying."""
    df = bh._enrich_df(base_then_breakout(breakout_close=1005.0), 60)
    assert df.iloc[-1]["Close"] < df.iloc[-1]["prior_high"]
    assert bh._build_signal("TEST", df, 100_000, 1.5, 25.0) is None


def test_breakout_without_volume_confirmation_is_rejected():
    df = bh._enrich_df(base_then_breakout(breakout_vol=1_000_000), 60)
    assert bh._build_signal("TEST", df, 100_000, 1.5, 25.0) is None


def test_breakout_out_of_a_wide_base_is_rejected():
    df = bh._enrich_df(base_then_breakout(base_amp=0.30, breakout_close=1400.0), 60)
    sig_loose = bh._build_signal("TEST", df, 100_000, 1.5, 100.0)
    assert sig_loose is not None and sig_loose["base_range_pct"] > 25.0
    assert bh._build_signal("TEST", df, 100_000, 1.5, 25.0) is None


def test_breakout_marks_a_52_week_high():
    df = bh._enrich_df(base_then_breakout(rows=150), 60)
    sig = bh._build_signal("TEST", df, 100_000, 1.5, 25.0)
    assert sig["is_52w_high"] is True


def test_breakout_scores_a_tight_entry_above_an_extended_one():
    tight = bh._build_signal("T", bh._enrich_df(base_then_breakout(breakout_close=1035.0), 60),
                             100_000, 1.5, 25.0)
    extended = bh._build_signal("T", bh._enrich_df(base_then_breakout(breakout_close=1200.0), 60),
                                100_000, 1.5, 25.0)
    assert tight["confidence_score"] > extended["confidence_score"]


async def test_breakout_rejects_an_out_of_range_lookback():
    assert (await bh.scan_breakout_high(lookback_days=5))["error"] is True
    assert (await bh.scan_breakout_high(lookback_days=999))["error"] is True


# ── gap ───────────────────────────────────────────────────────────────────────

def gap_frame(rows=60, prev=1000.0, open_=1050.0, close=1060.0,
              low=1045.0, high=1065.0, vol=2_000_000, base_vol=1_000_000):
    closes = np.full(rows, prev, dtype=float)
    closes[-1] = close
    opens = np.full(rows, prev, dtype=float)
    opens[-1] = open_
    highs = closes * 1.005
    highs[-1] = high
    lows = closes * 0.995
    lows[-1] = low
    vols = [base_vol] * (rows - 1) + [vol]
    return _frame(closes, volumes=vols, highs=highs, lows=lows, opens=opens)


def test_gap_up_that_held_is_reported():
    sig = gp._build_signal("TEST", gp._enrich_df(gap_frame()), 100_000, 2.0, "up")
    assert sig is not None
    assert sig["gap_direction"] == "up"
    assert sig["gap_unfilled"] is True
    assert sig["held_gap"] is True
    assert sig["gap_pct"] == pytest.approx(5.0, abs=0.01)


def test_gap_that_filled_back_through_the_prior_close_is_still_reported_but_unfilled_is_false():
    sig = gp._build_signal("TEST", gp._enrich_df(gap_frame(low=990.0)), 100_000, 2.0, "up")
    assert sig is not None and sig["gap_unfilled"] is False


def test_gap_up_that_gave_everything_back_is_rejected():
    sig = gp._build_signal("TEST", gp._enrich_df(gap_frame(close=995.0, low=990.0)),
                           100_000, 2.0, "up")
    assert sig is None


def test_gap_below_the_threshold_is_rejected():
    df = gp._enrich_df(gap_frame(open_=1005.0, close=1010.0, low=1002.0, high=1012.0))
    assert gp._build_signal("TEST", df, 100_000, 2.0, "up") is None


def test_gap_down_exhaustion_requires_a_close_above_the_open():
    reversal = gap_frame(open_=940.0, close=975.0, low=935.0, high=980.0)
    sig = gp._build_signal("TEST", gp._enrich_df(reversal), 100_000, 2.0, "down")
    assert sig is not None and sig["gap_direction"] == "down"

    continuation = gap_frame(open_=940.0, close=920.0, low=915.0, high=945.0)
    assert gp._build_signal("TEST", gp._enrich_df(continuation), 100_000, 2.0, "down") is None


def test_direction_filter_excludes_the_other_side():
    up = gp._enrich_df(gap_frame())
    assert gp._build_signal("TEST", up, 100_000, 2.0, "down") is None
    assert gp._build_signal("TEST", up, 100_000, 2.0, "both") is not None


@pytest.mark.parametrize("price,band", [(150.0, 35.0), (1000.0, 25.0), (9000.0, 20.0)])
def test_auto_reject_bands_follow_the_idx_price_tiers(price, band):
    assert gp.auto_reject_pct(price) == band


def test_gap_reports_its_share_of_the_auto_reject_band():
    sig = gp._build_signal("TEST", gp._enrich_df(gap_frame()), 100_000, 2.0, "up")
    # A 5% gap against the 25% band for a 1000 IDR stock.
    assert sig["auto_reject_band_pct"] == 25.0
    assert sig["gap_share_of_band_pct"] == pytest.approx(20.0, abs=0.5)


async def test_gap_rejects_an_unknown_direction():
    r = await gp.scan_gap(direction="sideways")
    assert r["error"] is True


# ── distribution warning ──────────────────────────────────────────────────────

def breakdown_frame(rows=280, peak=1400.0, end=900.0, peak_at=100, last_vol=3_000_000):
    up = np.linspace(1000.0, peak, peak_at)
    down = np.linspace(peak, end, rows - peak_at)
    closes = np.concatenate([up, down])
    vols = [1_000_000] * (rows - 1) + [last_vol]
    return _frame(closes, volumes=vols)


def test_distribution_flags_a_broken_chart():
    sig = dist._build_signal("TEST", dist._enrich_df(breakdown_frame()), 100_000, 50.0)
    assert sig is not None
    assert sig["below_sma50"] is True
    assert sig["death_cross"] is True
    assert {"close_below_sma50", "death_cross", "sma50_declining", "lower_high"} <= set(sig["warnings"])
    assert sig["confidence_score"] >= 50.0


def test_distribution_ignores_a_healthy_uptrend():
    healthy = _frame(np.linspace(1000, 1600, 280))
    assert dist._build_signal("TEST", dist._enrich_df(healthy), 100_000, 50.0) is None


def test_distribution_score_threshold_is_honoured():
    df = dist._enrich_df(breakdown_frame())
    assert dist._build_signal("TEST", df, 100_000, 0.0) is not None
    assert dist._build_signal("TEST", df, 100_000, 100.5) is None


def test_distribution_flags_heavy_volume_down_days():
    heavy = dist._build_signal("TEST", dist._enrich_df(breakdown_frame(last_vol=5_000_000)),
                               100_000, 0.0)
    quiet = dist._build_signal("TEST", dist._enrich_df(breakdown_frame(last_vol=200_000)),
                               100_000, 0.0)
    assert "heavy_volume_down_day" in heavy["warnings"]
    assert "heavy_volume_down_day" not in quiet["warnings"]


def test_distribution_works_without_a_full_sma200_history():
    """A recent listing should still raise the warnings its history supports."""
    short = breakdown_frame(rows=120, peak_at=40)
    df = dist._enrich_df(short)
    assert np.isnan(df.iloc[-1]["SMA200"])

    sig = dist._build_signal("TEST", df, 100_000, 20.0)
    assert sig is not None
    assert sig["death_cross"] is False
    assert "death_cross" not in sig["warnings"]
    assert "close_below_sma50" in sig["warnings"]


def test_distribution_score_ranks_severity_not_desirability():
    worse = dist._build_signal("T", dist._enrich_df(breakdown_frame(end=700.0)), 100_000, 0.0)
    milder = dist._build_signal("T", dist._enrich_df(breakdown_frame(end=1380.0)), 100_000, 0.0)
    assert worse["confidence_score"] >= (milder["confidence_score"] if milder else 0)


async def test_distribution_rejects_an_out_of_range_score():
    assert (await dist.scan_distribution_warning(min_warning_score=140))["error"] is True


# ── relative strength ─────────────────────────────────────────────────────────

def rs_pair(rows=200, stock_end=1400.0, index_end=1100.0):
    stock = _frame(np.linspace(1000.0, stock_end, rows))
    bench = pd.Series(np.linspace(1000.0, index_end, rows), index=stock.index)
    return stock, bench


def test_relative_strength_finds_a_leader():
    stock, bench = rs_pair()
    sig = rs._build_signal("TEST", stock, bench, 100_000, 5.0, False)
    assert sig is not None
    assert sig["excess_3m_pct"] > 0
    assert sig["return_3m_pct"] > sig["ihsg_return_3m_pct"]


def test_relative_strength_rejects_a_laggard():
    """The BMRI case: a rising stock that is rising slower than the index."""
    stock, bench = rs_pair(stock_end=1050.0, index_end=1300.0)
    sig = rs._build_signal("TEST", stock, bench, 100_000, 5.0, False)
    assert sig is None


def test_relative_strength_excess_threshold_is_a_real_filter():
    stock, bench = rs_pair()
    loose = rs._build_signal("TEST", stock, bench, 100_000, 0.0, False)
    assert loose is not None
    assert rs._build_signal("TEST", stock, bench, 100_000, loose["excess_3m_pct"] + 1, False) is None


def test_relative_strength_require_rs_high_rejects_a_faded_leader():
    """Outperformed earlier, but the RS line has since rolled over."""
    rows = 200
    stock = np.linspace(1000.0, 1600.0, rows)
    # Fade only the last 15 bars: still well ahead of the index over 3 months,
    # but the RS line peaked inside the 3-month window and has rolled over.
    stock[-15:] = np.linspace(1600.0, 1530.0, 15)
    bench = pd.Series(np.linspace(1000.0, 1100.0, rows))
    df = _frame(stock)
    bench.index = df.index

    lenient = rs._build_signal("TEST", df, bench, 100_000, 0.0, False)
    assert lenient is not None and lenient["rs_at_3mo_high"] is False
    assert rs._build_signal("TEST", df, bench, 100_000, 0.0, True) is None


def test_relative_strength_computes_beta_against_the_index():
    stock, bench = rs_pair()
    sig = rs._build_signal("TEST", stock, bench, 100_000, 0.0, False)
    assert sig["beta_63d"] is not None


def test_relative_strength_aligns_a_benchmark_with_missing_sessions():
    """The index print can be absent on a day the stock traded."""
    stock, bench = rs_pair()
    bench.iloc[-5] = np.nan
    sig = rs._build_signal("TEST", stock, bench, 100_000, 0.0, False)
    assert sig is not None, "a single missing index bar dropped the ticker"


def test_relative_strength_skips_tickers_with_too_little_history():
    stock, bench = rs_pair(rows=60)
    assert rs._build_signal("TEST", stock, bench, 100_000, 0.0, False) is None


def test_relative_strength_respects_the_volume_floor():
    stock, bench = rs_pair()
    assert rs._build_signal("TEST", stock, bench, 50_000_000, 0.0, False) is None


def test_breakout_funnel_counts_survivors_at_each_stage():
    """Zero signals must be explainable: a quiet market and a broken scan differ."""
    funnel = bh._new_funnel()
    passing = bh._enrich_df(base_then_breakout(), 60)
    failing = bh._enrich_df(base_then_breakout(breakout_close=1005.0), 60)

    bh._build_signal("PASS", passing, 100_000, 1.5, 25.0, funnel)
    bh._build_signal("FAIL", failing, 100_000, 1.5, 25.0, funnel)

    counts = funnel.to_dict()
    assert counts["enough_history"] == 2
    assert counts["passed_volume_floor"] == 2
    # Only the first cleared its base high, and it went on to clear every stage.
    assert counts["closed_above_prior_high"] == 1
    assert counts["volume_confirmed"] == 1


def test_breakout_funnel_is_optional():
    """The funnel is a diagnostic, not a required argument."""
    df = bh._enrich_df(base_then_breakout(), 60)
    assert bh._build_signal("TEST", df, 100_000, 1.5, 25.0) is not None


def test_distribution_never_emits_a_row_with_no_warnings():
    """min_warning_score=0 must not turn a risk scan into a list of every stock."""
    healthy = _frame(np.linspace(1000, 1600, 280))
    assert dist._build_signal("TEST", dist._enrich_df(healthy), 100_000, 0.0) is None


def test_distribution_funnel_separates_clean_stocks_from_low_scorers():
    from src.tools._scan_common import Funnel

    funnel = Funnel(*dist.FUNNEL_STAGES)
    healthy = dist._enrich_df(_frame(np.linspace(1000, 1600, 280)))
    broken = dist._enrich_df(breakdown_frame())

    dist._build_signal("HEALTHY", healthy, 100_000, 50.0, funnel)
    dist._build_signal("BROKEN", broken, 100_000, 50.0, funnel)

    counts = funnel.to_dict()
    assert counts["enough_history"] == 2
    assert counts["passed_volume_floor"] == 2
    assert counts["raised_any_warning"] == 1
    assert counts["scored_above_threshold"] == 1
