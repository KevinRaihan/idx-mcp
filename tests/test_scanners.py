"""Signal logic for the three v1.1 strategy scanners.

Frames are synthesised so each filter can be driven across its boundary without
depending on what the market happened to do today.
"""

import numpy as np
import pandas as pd
import pytest

from src.tools import mean_reversion as mr
from src.tools import vol_squeeze as vs
from src.tools import volume_accumulation as va
from src.tools._scan_common import Funnel, build_envelope


def frame(closes, volumes=None, spread_pct=1.0, highs=None, lows=None):
    n = len(closes)
    idx = pd.bdate_range(end="2026-08-26", periods=n)
    volumes = volumes if volumes is not None else [5_000_000] * n
    half = spread_pct / 200.0
    return pd.DataFrame(
        {
            "Open": closes,
            "High": highs if highs is not None else [c * (1 + half) for c in closes],
            "Low": lows if lows is not None else [c * (1 - half) for c in closes],
            "Close": closes,
            "Volume": volumes,
        },
        index=idx,
    )


# ── mean reversion ────────────────────────────────────────────────────────────

def test_rsi_pins_to_100_when_there_are_no_down_closes():
    """avg_loss == 0 used to divide to NaN and silently drop the ticker."""
    rising = pd.Series(np.linspace(100, 200, 60))
    rsi = mr._compute_rsi(rising)
    assert not np.isnan(rsi.iloc[-1])
    assert rsi.iloc[-1] == pytest.approx(100.0)


def test_rsi_is_zero_on_an_unbroken_decline():
    falling = pd.Series(np.linspace(200, 100, 60))
    assert mr._compute_rsi(falling).iloc[-1] == pytest.approx(0.0)


def test_rsi_on_a_flat_series_is_neutral():
    assert mr._compute_rsi(pd.Series([100.0] * 60)).iloc[-1] == pytest.approx(50.0)


def test_rsi_stays_within_bounds_on_mixed_data():
    rng = np.random.default_rng(7)
    series = pd.Series(1000 + np.cumsum(rng.normal(0, 10, 300)))
    rsi = mr._compute_rsi(series).dropna()
    assert rsi.between(0, 100).all()


def test_mean_reversion_fires_on_a_deep_oversold_selloff():
    closes = [1000.0] * 40 + list(np.linspace(1000, 700, 20))
    df = mr._enrich_df(frame(closes))
    sig = mr._build_signal("TEST", df, rsi_thresh=30.0, min_vol=500_000, min_below_pct=5.0)

    assert sig is not None
    assert sig["rsi"] < 30.0
    assert sig["distance_below_sma20_pct"] > 5.0
    assert 0 < sig["confidence_score"] <= 100


def test_mean_reversion_ignores_a_stock_near_its_mean():
    df = mr._enrich_df(frame([1000.0] * 60))
    assert mr._build_signal("TEST", df, 30.0, 500_000, 5.0) is None


def test_mean_reversion_respects_the_volume_floor():
    closes = [1000.0] * 40 + list(np.linspace(1000, 700, 20))
    df = mr._enrich_df(frame(closes, volumes=[10_000] * 60))
    assert mr._build_signal("TEST", df, 30.0, 500_000, 5.0) is None


def test_mean_reversion_rejects_frames_that_are_too_short():
    df = mr._enrich_df(frame(list(np.linspace(1000, 700, 30))))
    assert mr._build_signal("TEST", df, 30.0, 500_000, 5.0) is None


def test_stricter_distance_threshold_filters_the_signal_out():
    closes = [1000.0] * 40 + list(np.linspace(1000, 880, 20))
    df = mr._enrich_df(frame(closes))
    assert mr._build_signal("TEST", df, 30.0, 500_000, 1.0) is not None
    assert mr._build_signal("TEST", df, 30.0, 500_000, 40.0) is None


# ── volatility squeeze ────────────────────────────────────────────────────────

def _squeeze_frame():
    """Wide, noisy history that tightens into a quiet drift at the end."""
    rng = np.random.default_rng(3)
    noisy = 1000 + np.cumsum(rng.normal(0, 25, 200))
    calm = [float(noisy[-1]) + 0.05 * i for i in range(1, 61)]
    return frame(list(noisy) + calm)


def test_volatility_squeeze_fires_when_bands_tighten_and_macd_turns_up():
    df = vs._enrich_df(_squeeze_frame())
    sig = vs._build_signal("TEST", df, min_vol=1_000_000, tolerance=1.10)

    assert sig is not None
    assert sig["bb_width"] <= sig["bb_width_6mo_min"] * 1.10
    assert sig["macd_hist"] > sig["macd_hist_prev"]


def test_volatility_squeeze_ignores_a_wide_ranging_stock():
    rng = np.random.default_rng(11)
    calm = 1000 + np.cumsum(rng.normal(0, 1, 200))
    wild = 1000 + np.cumsum(rng.normal(0, 60, 60))
    df = vs._enrich_df(frame(list(calm) + list(wild)))
    assert vs._build_signal("TEST", df, 1_000_000, 1.10) is None


def test_volatility_squeeze_requires_rising_momentum():
    """Falling MACD histogram must not qualify, however tight the bands are."""
    df = vs._enrich_df(_squeeze_frame())
    df.loc[df.index[-1], "macd_hist"] = df["macd_hist"].iloc[-2] - 1.0
    assert vs._build_signal("TEST", df, 1_000_000, 1.10) is None


def test_volatility_squeeze_missing_previous_bar_is_rejected_not_assumed_zero():
    df = vs._enrich_df(_squeeze_frame())
    df.loc[df.index[-2], "macd_hist"] = np.nan
    assert vs._build_signal("TEST", df, 1_000_000, 1.10) is None


def test_volatility_squeeze_honours_the_volume_floor():
    df = vs._enrich_df(_squeeze_frame())
    assert vs._build_signal("TEST", df, 10**12, 1.10) is None


# ── volume accumulation ───────────────────────────────────────────────────────

def _accumulation_frame(last_volume=30_000_000, spread_pct=1.0, last_close=1002.0):
    closes = [1000.0] * 39 + [last_close]
    volumes = [5_000_000] * 39 + [last_volume]
    df = frame(closes, volumes=volumes, spread_pct=0.2)
    half = spread_pct / 200.0
    df.loc[df.index[-1], "High"] = last_close * (1 + half)
    df.loc[df.index[-1], "Low"] = last_close * (1 - half)
    return df


def test_volume_accumulation_fires_on_a_tight_high_volume_bar():
    df = va._enrich_df(_accumulation_frame())
    sig = va._build_signal("TEST", df, 1_000_000, 3.0, 5.0)

    assert sig is not None
    assert sig["volume_ratio"] == pytest.approx(6.0)
    assert sig["intraday_spread_pct"] < 5.0
    assert sig["change_pct"] > 0


def test_volume_baseline_excludes_the_current_bar():
    """Including today's spike in its own average would deflate the ratio."""
    df = va._enrich_df(_accumulation_frame(last_volume=30_000_000))
    assert df["vol_20d_avg"].iloc[-1] == pytest.approx(5_000_000)


def test_volume_accumulation_rejects_a_wide_range_bar():
    df = va._enrich_df(_accumulation_frame(spread_pct=12.0))
    assert va._build_signal("TEST", df, 1_000_000, 3.0, 5.0) is None


def test_volume_accumulation_rejects_a_down_close():
    df = va._enrich_df(_accumulation_frame(last_close=940.0))
    assert va._build_signal("TEST", df, 1_000_000, 3.0, 5.0) is None


def test_volume_accumulation_rejects_ordinary_volume():
    df = va._enrich_df(_accumulation_frame(last_volume=6_000_000))
    assert va._build_signal("TEST", df, 1_000_000, 3.0, 5.0) is None


def test_volume_accumulation_scan_period_covers_min_rows():
    """period='1mo' yields ~22 bars against MIN_ROWS=25, so the scan found nothing."""
    assert va.SCAN_PERIOD == "3mo"
    assert va.MIN_ROWS >= 21


def test_relaxing_the_spread_threshold_admits_the_signal():
    df = va._enrich_df(_accumulation_frame(spread_pct=7.0))
    assert va._build_signal("TEST", df, 1_000_000, 3.0, 5.0) is None
    assert va._build_signal("TEST", df, 1_000_000, 3.0, 10.0) is not None


# ── shared envelope ───────────────────────────────────────────────────────────

def test_envelope_reports_counts_and_truncates_to_top_n():
    signals = [{"ticker": f"T{i}", "confidence_score": i} for i in range(25)]
    env = build_envelope(
        strategy="demo", signals=signals, total_scanned=178, downloaded=169,
        failed=9, filters={"min_volume": 1}, elapsed_s=12.34, top_n=10,
    )
    assert env["signals_found"] == 25
    assert len(env["top_10"]) == 10
    assert env["top_10"] == env["signals"]
    assert env["universe_size"] == 178
    assert env["tickers_without_data"] == 9
    assert env["elapsed_seconds"] == 12.3
    assert "not financial advice" in env["disclaimer"].lower()


@pytest.mark.parametrize("scan", [
    mr.scan_mean_reversion, vs.scan_volatility_squeeze, va.scan_volume_accumulation,
])
async def test_scanners_reject_non_numeric_parameters(scan):
    result = await scan("not-a-number")
    assert result["error"] is True


async def test_scan_results_are_cached(monkeypatch):
    calls = []

    def fake_scan(*args, **kwargs):
        calls.append(args)
        return build_envelope(
            strategy="mean_reversion", signals=[], total_scanned=1, downloaded=1,
            failed=0, filters={}, elapsed_s=0.1,
        )

    monkeypatch.setattr(mr, "_run_full_scan", fake_scan)
    await mr.scan_mean_reversion()
    await mr.scan_mean_reversion()
    assert len(calls) == 1

    await mr.scan_mean_reversion(rsi_threshold=20.0)
    assert len(calls) == 2


# ── all_signals and the funnel ────────────────────────────────────────────────

def test_all_signals_is_complete_while_top_10_is_capped():
    """The truncation bug: signals_found said 134, the payload carried 10 rows."""
    signals = [{"ticker": f"T{i}", "confidence_score": i} for i in range(134)]
    env = build_envelope(
        strategy="demo", signals=signals, total_scanned=178, downloaded=169,
        failed=9, filters={}, elapsed_s=1.0, top_n=10,
    )
    assert env["signals_found"] == 134
    assert len(env["top_10"]) == 10
    assert len(env["all_signals"]) == 134
    assert len(env["all_signals"]) == env["signals_found"]


def test_all_signals_lets_a_ticker_outside_the_top_10_be_found():
    """The veto use case: is ticker X flagged, even at rank 90?"""
    signals = [{"ticker": f"T{i}", "confidence_score": 100 - i} for i in range(100)]
    env = build_envelope(
        strategy="demo", signals=signals, total_scanned=178, downloaded=178,
        failed=0, filters={}, elapsed_s=1.0, top_n=10,
    )
    flagged = {s["ticker"] for s in env["all_signals"]}
    assert "T90" in flagged
    assert "T90" not in {s["ticker"] for s in env["top_10"]}


def test_envelope_omits_the_funnel_when_none_is_supplied():
    env = build_envelope(
        strategy="demo", signals=[], total_scanned=1, downloaded=1,
        failed=0, filters={}, elapsed_s=1.0,
    )
    assert "filter_funnel" not in env


def test_envelope_serialises_a_funnel_object():
    funnel = Funnel("a", "b")
    funnel.passed("a")
    env = build_envelope(
        strategy="demo", signals=[], total_scanned=1, downloaded=1,
        failed=0, filters={}, elapsed_s=1.0, funnel=funnel,
    )
    assert env["filter_funnel"] == {"a": 1, "b": 0}


def test_funnel_counts_and_starts_at_zero():
    f = Funnel("one", "two")
    assert f.to_dict() == {"one": 0, "two": 0}
    f.passed("one")
    f.passed("one")
    f.passed("two")
    assert f.to_dict() == {"one": 2, "two": 1}


def test_funnel_rejects_an_undeclared_stage():
    """A typo'd stage would otherwise report a funnel that disagrees with the code."""
    f = Funnel("declared")
    with pytest.raises(KeyError):
        f.passed("typo")


def test_funnel_dict_is_a_copy():
    f = Funnel("a")
    out = f.to_dict()
    out["a"] = 999
    assert f.to_dict() == {"a": 0}


# ── degraded quote reporting ──────────────────────────────────────────────────

async def test_price_flags_a_partial_quote_when_the_info_endpoint_is_refused(monkeypatch):
    """Yahoo's quote endpoint is rate-limited apart from the chart endpoint."""
    import src.tools.price as price_mod

    class _FastInfo:
        last_price = 6475.0

    class _Stock:
        info = {}                      # what an unauthorized quote call returns
        fast_info = _FastInfo()

    monkeypatch.setattr(price_mod.yf, "Ticker", lambda t: _Stock())
    r = await price_mod.get_stock_price("BBCA")

    assert r["price"] == 6475.0
    assert r["partial"] is True
    assert r["source"] == "yfinance:fast_info"
    assert "week_52_low" in r["missing_fields"]
    assert r["partial_reason"]


async def test_price_reports_a_complete_quote_as_not_partial(monkeypatch):
    import src.tools.price as price_mod

    class _Stock:
        info = {
            "regularMarketPrice": 6475.0, "regularMarketPreviousClose": 6400.0,
            "regularMarketOpen": 6410.0, "regularMarketDayHigh": 6500.0,
            "regularMarketDayLow": 6390.0, "regularMarketVolume": 1_000_000,
            "marketCap": 800_000_000_000_000, "fiftyTwoWeekHigh": 7000.0,
            "fiftyTwoWeekLow": 5500.0, "longName": "Bank Central Asia",
        }

    monkeypatch.setattr(price_mod.yf, "Ticker", lambda t: _Stock())
    r = await price_mod.get_stock_price("BBCA")

    assert r["partial"] is False
    assert "missing_fields" not in r
    assert r["source"] == "yfinance"
    assert r["week_52_low"] <= r["price"] <= r["week_52_high"]
