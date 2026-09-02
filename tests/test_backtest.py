"""Replaying every scan strategy over history.

The load-bearing property is that enriching once and slicing gives the same
answer as enriching each slice. If it ever stops holding, the backtest is
reading the future and every base rate it reports is worthless -- so it is
asserted here rather than trusted.
"""

import json

import numpy as np
import pandas as pd
import pytest

from src.tools import backtest as bt


def synthetic(n=600, seed=7):
    """A frame with enough shape to fire several strategies."""
    rng = np.random.default_rng(seed)
    drift = np.linspace(0, 0.9, n)
    noise = rng.normal(0, 0.02, n).cumsum()
    close = 1_000 * np.exp(drift * 0.5 + noise)
    high = close * (1 + rng.uniform(0.001, 0.03, n))
    low = close * (1 - rng.uniform(0.001, 0.03, n))
    openp = close * (1 + rng.normal(0, 0.01, n))
    volume = rng.integers(2_000_000, 20_000_000, n).astype(float)
    idx = pd.bdate_range("2023-01-02", periods=n)
    return pd.DataFrame(
        {"Open": openp, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=idx,
    )


class TestRegistry:
    def test_every_scan_tool_has_a_backtest(self):
        from src import server

        scans = {t.name for t in server.TOOLS if t.name.startswith("scan_")}
        assert scans == set(bt.REGISTRY), (
            "a scan without a registry entry ships with no base rate"
        )

    def test_registry_defaults_match_the_scanner_module_defaults(self):
        """A backtest run at different thresholds measures a different strategy."""
        import src.tools.mean_reversion as mr
        import src.tools.trend_pullback as tp

        assert (bt.REGISTRY["scan_mean_reversion"].defaults["rsi_threshold"]
                == mr.DEFAULT_RSI_THRESH)
        assert (bt.REGISTRY["scan_trend_pullback"].defaults["max_pullback_pct"]
                == tp.DEFAULT_MAX_PULLBACK_PCT)

    def test_every_declared_param_is_resolvable(self):
        for name, s in bt.REGISTRY.items():
            opts = bt._resolve_options(s, None)
            for p in s.param_names + s.extra_enrich_params:
                assert p in opts, f"{name}: {p} has no default"

    def test_the_old_ma_ketat_only_backtest_is_gone(self):
        """Two implementations of one measurement are free to disagree."""
        import src.tools.scanner as scanner

        assert not hasattr(scanner, "run_backtest")
        assert not hasattr(scanner, "_backtest_single")


class TestCausality:
    """Enrich-once-then-slice must equal enrich-the-slice.

    Compared at the *frame* level, not the signal level. Comparing built
    signals looked equivalent but was vacuous: a strategy that fires rarely
    returns None on both sides, and None == None proves nothing. Verified
    against a deliberately leaky indicator -- the frame comparison catches it,
    the signal comparison did not.
    """

    @staticmethod
    def _same_last_row(a: pd.DataFrame, b: pd.DataFrame) -> bool:
        assert list(a.columns) == list(b.columns)
        left, right = a.iloc[-1], b.iloc[-1]
        for col in a.columns:
            x, y = left[col], right[col]
            if pd.isna(x) and pd.isna(y):
                continue
            if pd.isna(x) != pd.isna(y) or not np.isclose(float(x), float(y), rtol=1e-9):
                return False
        return True

    @pytest.mark.parametrize("name", [n for n, s in bt.REGISTRY.items() if s.enrich])
    def test_slicing_after_enrichment_does_not_leak_the_future(self, name):
        s = bt.REGISTRY[name]
        df = synthetic()
        opts = bt._resolve_options(s, None)
        extra = [opts[n] for n in s.extra_enrich_params]

        compared = 0
        for i in range(s.min_rows, len(df) - 7, 41):
            sliced = s.enrich(df, *extra).iloc[: i + 1]
            fresh = s.enrich(df.iloc[: i + 1], *extra)
            assert self._same_last_row(sliced, fresh), (
                f"{name} disagrees at session {i}: an indicator is not causal, so "
                f"the backtest would read data the live scan could not have had"
            )
            compared += 1
        assert compared > 3, "sampled too few sessions to mean anything"

    def test_the_check_detects_an_acausal_indicator(self):
        """Teeth: a whole-series extremum must be caught.

        The close rises monotonically so the whole-series maximum is strictly
        greater than any prefix maximum. On a falling series the two coincide
        and the leak hides -- which is exactly how the earlier signal-level
        version of this check passed while proving nothing.
        """
        s = bt.REGISTRY["scan_mean_reversion"]
        n = 400
        rising = pd.DataFrame(
            {"Open": np.arange(n) + 100.0, "High": np.arange(n) + 101.0,
             "Low": np.arange(n) + 99.0, "Close": np.arange(n) + 100.0,
             "Volume": np.full(n, 5_000_000.0)},
            index=pd.bdate_range("2023-01-02", periods=n),
        )

        def leaky(frame):
            out = s.enrich(frame)
            out["SMA20"] = out["Close"].max()   # reads the whole series
            return out

        i = 300
        assert leaky(rising)["SMA20"].iloc[-1] > leaky(rising.iloc[: i + 1])["SMA20"].iloc[-1]
        assert not self._same_last_row(
            leaky(rising).iloc[: i + 1], leaky(rising.iloc[: i + 1])
        )
        # And the causal original passes on the same frame.
        assert self._same_last_row(
            s.enrich(rising).iloc[: i + 1], s.enrich(rising.iloc[: i + 1])
        )

    def test_relative_strength_is_causal_against_its_benchmark(self):
        """No _enrich_df, so it is checked at the signal level with a fake index."""
        s = bt.REGISTRY["scan_relative_strength"]
        df = synthetic()
        bench = pd.Series(
            np.linspace(6_000, 7_000, len(df)) + np.sin(np.arange(len(df)) / 9) * 40,
            index=df.index,
        )
        params = tuple(bt._resolve_options(s, None)[n] for n in s.param_names)

        fired = 0
        for i in range(s.min_rows, len(df) - 7, 41):
            window = df.iloc[: i + 1]
            a = s.build("TEST", window, bench, *params)
            b = s.build("TEST", window, bench.iloc[: i + 1], *params)
            assert json.dumps(a, sort_keys=True, default=str) == \
                   json.dumps(b, sort_keys=True, default=str), (
                f"truncating the benchmark changed the signal at {i}: "
                f"future index values are reaching the calculation"
            )
            fired += a is not None
        assert fired > 0, "no signal fired, so the comparison proved nothing"


class TestDirectionality:
    def test_a_risk_signal_is_scored_on_price_falling(self):
        s = bt.REGISTRY["scan_distribution_warning"]
        assert s.direction == bt.RISK
        assert bt._signal_is_bullish({}, s) is False

    def test_a_long_signal_is_scored_on_price_rising(self):
        assert bt._signal_is_bullish({}, bt.REGISTRY["scan_mean_reversion"]) is True

    def test_a_gap_reads_its_direction_off_the_signal(self):
        """scan_gap emits both ways; a gap-down is not a failed long."""
        s = bt.REGISTRY["scan_gap"]
        assert bt._signal_is_bullish({"direction": "up"}, s) is True
        assert bt._signal_is_bullish({"direction": "down"}, s) is False

    def test_returns_are_signed_from_the_strategys_point_of_view(self):
        trades = [{"strategy_return_pct": 2.0}, {"strategy_return_pct": -1.0}]
        out = bt.summarise(trades, 7)
        assert out["win_rate_pct"] == 50.0
        assert out["avg_return_pct"] == 0.5


class TestSummarise:
    def test_no_trades_yields_nulls_not_zeros(self):
        out = bt.summarise([], 7)
        assert out["n_signals"] == 0
        assert out["win_rate_pct"] is None
        assert out["avg_return_pct"] is None

    def test_median_is_reported_alongside_the_mean(self):
        trades = [{"strategy_return_pct": r} for r in (1.0, 1.0, 1.0, 1.0, 40.0)]
        out = bt.summarise(trades, 7)
        assert out["median_return_pct"] == 1.0
        assert out["avg_return_pct"] == 8.8
        assert out["best_return_pct"] == 40.0


class TestReplay:
    def test_replay_measures_the_declared_horizon(self):
        s = bt.REGISTRY["scan_mean_reversion"]
        df = synthetic()
        opts = bt._resolve_options(s, None)
        trades = bt.replay(s, "TEST", df, opts, horizon=7)
        enriched = s.enrich(df)
        closes = enriched["Close"]
        for t in trades[:5]:
            i = list(enriched.index.astype(str).str[:10]).index(t["date"])
            expected = (closes.iloc[i + 7] - closes.iloc[i]) / closes.iloc[i] * 100
            assert t["price_change_pct"] == pytest.approx(round(expected, 2), abs=0.01)

    def test_replay_never_reads_past_the_end(self):
        s = bt.REGISTRY["scan_mean_reversion"]
        df = synthetic(n=300)
        trades = bt.replay(s, "TEST", df, bt._resolve_options(s, None), horizon=7)
        dates = set(df.index.astype(str).str[:10])
        assert all(t["date"] in dates for t in trades)
        last_scoreable = str(df.index[len(df) - 7 - 1].date())
        assert all(t["date"] <= last_scoreable for t in trades)


class TestInvalidInput:
    async def test_an_unknown_strategy_is_rejected_with_the_valid_names(self):
        r = await bt.run_backtest_all("BBCA", strategy="scan_nope")
        assert r["error"] is True
        assert r["error_type"] == "invalid_arguments"
        assert "scan_mean_reversion" in r["message"]

    async def test_a_bad_ticker_is_rejected_before_any_fetch(self):
        r = await bt.run_backtest_all("not a ticker!!")
        assert r["error"] is True
        assert r["error_type"] == "invalid_ticker"
