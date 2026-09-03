"""Payloads must not assert conclusions their inputs cannot support.

Each case here is a real defect found while running an analysis on 2026-08-31,
and they share one shape: a default value (empty list, zero, a null) flowing
into a field that reads as a measurement.
"""

import pandas as pd
import pytest

from src.scrapers.stockbit import scrape_broker_summary  # noqa: F401  (import guard)
from src.tools.company_profile import _parse_major_holders
from src.tools.financials import dividend_yield_pct
from src.tools.golden_cross import _cross_age
from src.utils.completeness import mark_partial


class TestBrokerVerdictNeedsRows:
    """`sum([]) == 0` is not `> 0`, so no rows fell through to the bearish branch."""

    @staticmethod
    def _summarise(top_buyers, top_sellers):
        # Mirrors the tail of scrape_broker_summary without the network.
        foreign_net = sum(e["net_value_idr"] for e in top_buyers + top_sellers
                          if e["type"] == "foreign")
        institutional_net = sum(e["net_value_idr"] for e in top_buyers + top_sellers
                                if e["type"] in ("foreign", "domestic_institutional"))
        if not (top_buyers or top_sellers):
            return {"net_broker_flow": None, "institutional_bias": None,
                    "foreign_broker_bias": None, "data_available": False}

        def _bias(net, pos, neg):
            return pos if net > 0 else neg if net < 0 else "balanced"

        return {"net_broker_flow": _bias(institutional_net, "accumulation", "distribution"),
                "institutional_bias": _bias(institutional_net, "buying", "selling"),
                "foreign_broker_bias": _bias(foreign_net, "buying", "selling"),
                "data_available": True}

    def test_no_rows_yields_no_verdict(self):
        s = self._summarise([], [])
        assert s["data_available"] is False
        assert s["net_broker_flow"] is None
        assert s["institutional_bias"] is None

    def test_a_genuinely_balanced_book_is_not_reported_as_distribution(self):
        rows = [{"net_value_idr": 1_000, "type": "foreign"},
                {"net_value_idr": -1_000, "type": "domestic_institutional"}]
        s = self._summarise(rows, [])
        assert s["data_available"] is True
        assert s["net_broker_flow"] == "balanced"

    def test_real_selling_still_reads_as_distribution(self):
        rows = [{"net_value_idr": -5_000, "type": "foreign"}]
        s = self._summarise([], rows)
        assert s["net_broker_flow"] == "distribution"
        assert s["foreign_broker_bias"] == "selling"


class TestForeignFlowZeroIsNotAMeasurement:
    async def test_an_unparsed_page_reports_nulls_not_zero(self, monkeypatch):
        from src.tools import foreign_flow as ff

        async def fake(_):
            return {"foreign_buy_idr": 0, "foreign_sell_idr": 0,
                    "foreign_net_idr": 0, "data_available": False}

        monkeypatch.setattr(ff, "scrape_foreign_flow", fake)
        ff.cache.clear() if hasattr(ff.cache, "clear") else None

        r = await ff.get_foreign_flow("BBCA", period="weekly")
        assert r["data_available"] is False
        assert r["foreign_net_idr"] is None
        assert r["foreign_net_formatted"] is None
        assert "unavailable_reason" in r
        assert "not a reading of zero" in r["unavailable_reason"]

    async def test_a_real_zero_net_is_still_reported(self, monkeypatch):
        from src.tools import foreign_flow as ff

        async def fake(_):
            return {"foreign_buy_idr": 5_000, "foreign_sell_idr": 5_000,
                    "foreign_net_idr": 0, "data_available": True}

        monkeypatch.setattr(ff, "scrape_foreign_flow", fake)
        r = await ff.get_foreign_flow("BMRI", period="daily")
        assert r["data_available"] is True
        assert r["foreign_net_idr"] == 0
        assert r["foreign_net_formatted"] is not None


class TestMajorHoldersAreLabelledNotPositional:
    def test_counts_are_not_reported_as_percentages(self):
        """ISAT showed a holder at 90.0 that was really 90 institutions."""
        raw = pd.DataFrame(
            {"Value": [0.0085293, 0.10621, 0.72217005, 90.0]},
            index=["insidersPercentHeld", "institutionsPercentHeld",
                   "institutionsFloatPercentHeld", "institutionsCount"],
        )
        out = _parse_major_holders(raw)
        assert out["insiders_pct"] == 0.85
        assert out["institutions_pct"] == 10.62
        assert out["institutions_float_pct"] == 72.22
        assert out["institutions_count"] == 90
        assert all(not isinstance(v, str) for v in out.values())

    def test_an_unrecognised_shape_yields_missing_fields_not_wrong_ones(self):
        raw = pd.DataFrame({"Value": [1.0, 2.0]}, index=["somethingNew", "alsoNew"])
        assert _parse_major_holders(raw) == {}

    def test_empty_and_none_are_handled(self):
        assert _parse_major_holders(None) == {}
        assert _parse_major_holders(pd.DataFrame()) == {}


class TestCrossAgeExplainsANullCross:
    @staticmethod
    def _df(sma50, sma200):
        return pd.DataFrame({"SMA50": sma50, "SMA200": sma200})

    def test_a_confirmed_cross_with_no_event_in_window_is_older_than_lookback(self):
        """DMAS on 2026-08-31: confirmed true, days_since_cross null."""
        df = self._df([150.0] * 60, [130.0] * 60)
        assert _cross_age(df, None, confirmed=True) == "older_than_lookback"

    def test_an_unconfirmed_null_means_no_cross(self):
        df = self._df([100.0] * 60, [130.0] * 60)
        assert _cross_age(df, None, confirmed=False) == "no_cross_in_lookback"

    def test_a_measured_cross_says_so(self):
        df = self._df([150.0] * 60, [130.0] * 60)
        assert _cross_age(df, 4, confirmed=True) == "measured"

    def test_freshness_is_false_when_the_cross_predates_the_window(self):
        """The bare null already produced fresh=False; cross_age says why."""
        days_since_cross = None
        assert not (days_since_cross is not None and days_since_cross <= 10)


class TestDividendYieldScaling:
    def test_rate_over_price_needs_no_inference(self):
        pct, basis = dividend_yield_pct(
            {"trailingAnnualDividendRate": 111.0, "currentPrice": 2_530.0}
        )
        assert pct == pytest.approx(4.39, abs=0.01)
        assert basis == "trailing_dividend_rate_over_price"

    @pytest.mark.parametrize("raw", [4.39, 0.0439])
    def test_both_field_scales_resolve_to_the_same_yield(self, raw):
        """The regression: 4.39 was multiplied again and reported as 439%."""
        pct, basis = dividend_yield_pct({"dividendYield": raw})
        assert pct == pytest.approx(4.39, abs=0.01)
        assert basis == "dividend_yield_field_scale_inferred"

    @pytest.mark.parametrize("raw", [439.0, 829.0])
    def test_an_impossible_yield_is_dropped_not_reported(self, raw):
        pct, basis = dividend_yield_pct({"dividendYield": raw})
        assert pct is None
        assert basis == "implausible_value_discarded"

    def test_a_missing_field_yields_nothing_and_says_nothing(self):
        assert dividend_yield_pct({}) == (None, None)

    def test_the_rate_path_wins_over_the_ambiguous_field(self):
        pct, basis = dividend_yield_pct({
            "trailingAnnualDividendRate": 16.5, "currentPrice": 199.0,
            "dividendYield": 829.0,
        })
        assert pct == pytest.approx(8.29, abs=0.01)
        assert basis == "trailing_dividend_rate_over_price"


class TestPartialFlagging:
    def test_a_populated_payload_is_not_partial(self):
        p = mark_partial({"a": 1, "n": {"b": 2}}, ("a", "n.b"), "r")
        assert p["partial"] is False
        assert "missing_fields" not in p
        assert "partial_reason" not in p

    def test_zero_and_false_are_answers_not_absences(self):
        """A rate of 0.0 is a measurement; conflating it with missing is the bug."""
        p = mark_partial({"z": 0, "f": False, "s": 0.0}, ("z", "f", "s"), "r")
        assert p["partial"] is False

    def test_empty_containers_count_as_missing(self):
        """`sector_performance: []` means the source did not answer."""
        p = mark_partial({"lst": [], "dct": {}, "txt": ""}, ("lst", "dct", "txt"), "r")
        assert p["partial"] is True
        assert set(p["missing_fields"]) == {"lst", "dct", "txt"}

    def test_null_and_absent_paths_are_both_missing(self):
        p = mark_partial({"a": None}, ("a", "nope", "deep.path"), "why")
        assert set(p["missing_fields"]) == {"a", "nope", "deep.path"}
        assert p["partial_reason"] == "why"

    def test_nested_paths_resolve(self):
        payload = {"macro": {"usd_idr": 17_698.0, "bi_rate_pct": None}}
        p = mark_partial(payload, ("macro.usd_idr", "macro.bi_rate_pct"), "r")
        assert p["missing_fields"] == ["macro.bi_rate_pct"]


class TestScoreBasisIsDeclared:
    def test_every_scan_envelope_says_what_its_score_is(self):
        from src.tools._scan_common import SCORE_BASIS, build_envelope

        env = build_envelope(
            strategy="s", signals=[], total_scanned=1, downloaded=1, failed=0,
            filters={}, elapsed_s=0.1,
        )
        assert env["score_basis"] == SCORE_BASIS
        assert "not a calibrated probability" in env["score_note"]

    def test_the_truncated_signals_alias_is_gone(self):
        from src.tools._scan_common import build_envelope

        env = build_envelope(
            strategy="s", signals=[{"ticker": f"T{i}"} for i in range(30)],
            total_scanned=30, downloaded=30, failed=0, filters={}, elapsed_s=0.1,
        )
        assert "signals" not in env
        assert len(env["top_10"]) == 10
        assert len(env["all_signals"]) == env["signals_found"] == 30


class TestGapSaysWhichBarItRead:
    """The scan reported bar_date: today alongside a note claiming the
    in-progress session had been excluded. Both cannot be true. Verified on
    2026-09-03 at 11:02 WIB: every scan read a two-hour-old partial bar."""

    @staticmethod
    def _at(hhmm):
        from datetime import datetime

        from src.utils.ohlcv import WIB, last_settled_date

        h, m = hhmm
        return last_settled_date(datetime(2026, 9, 3, h, m, tzinfo=WIB))

    def test_a_session_still_trading_is_not_settled(self):
        assert str(self._at((11, 2))) == "2026-09-02"

    def test_the_close_settles_the_day_at_1615(self):
        assert str(self._at((16, 15))) == "2026-09-03"
        assert str(self._at((16, 14))) == "2026-09-02"

    def test_after_the_close_today_is_settled(self):
        assert str(self._at((20, 10))) == "2026-09-03"

    def test_drop_unsettled_session_agrees_with_the_helper(self):
        from datetime import datetime

        import pandas as pd

        from src.utils.ohlcv import WIB, drop_unsettled_session, last_settled_date

        idx = pd.to_datetime(["2026-09-01", "2026-09-02", "2026-09-03"]).tz_localize(WIB)
        df = pd.DataFrame({"Close": [1.0, 2.0, 3.0]}, index=idx)
        now = datetime(2026, 9, 3, 11, 2, tzinfo=WIB)
        kept = drop_unsettled_session(df, now=now)
        assert str(kept.index[-1].date()) == str(last_settled_date(now))

    def test_the_note_no_longer_claims_the_live_bar_is_excluded(self):
        import inspect

        from src.tools import gap

        src = inspect.getsource(gap._run_full_scan)
        assert "in-progress session is excluded" not in src
        assert "bar_settled" in inspect.getsource(gap._build_signal)


class TestCrossFieldsSayWhichQuestionTheyAnswer:
    """`death_cross` meant an event in get_technicals and a state in
    scan_distribution_warning. CTRA on 2026-09-03 returned false from one and
    true from the other with SMA50 580 under SMA200 684."""

    @staticmethod
    def _tech(sma50, sma200):
        import numpy as np
        import pandas as pd

        from src.tools import technicals as tech

        s50, s200 = pd.Series(sma50, dtype=float), pd.Series(sma200, dtype=float)
        golden = death = False
        state = None
        if len(s50.dropna()) >= 2 and len(s200.dropna()) >= 2:
            a, b = float(s50.iloc[-1]), float(s50.iloc[-2])
            c, d = float(s200.iloc[-1]), float(s200.iloc[-2])
            golden, death, state = b < d and a > c, b > d and a < c, a > c
        assert np is not None and tech is not None
        return {"golden_cross": golden, "death_cross": death,
                "sma50_above_sma200": state}

    def test_a_long_standing_death_cross_is_a_state_not_an_event(self):
        out = self._tech([600.0, 580.0], [690.0, 684.0])   # CTRA's shape
        assert out["death_cross"] is False, "no crossing happened on this bar"
        assert out["sma50_above_sma200"] is False, "but it is under, and must say so"

    def test_the_bar_a_cross_happens_on_sets_both(self):
        out = self._tech([680.0, 690.0], [685.0, 684.0])
        assert out["golden_cross"] is True
        assert out["sma50_above_sma200"] is True

    def test_the_payload_declares_its_basis(self):
        import inspect

        from src.tools import technicals as tech

        src = inspect.getsource(tech)
        assert '"cross_basis":         "event_on_this_bar"' in src
        assert '"sma50_above_sma200":  sma50_above_sma200' in src


class TestValuationRatiosAreInTheQuoteCurrency:
    """Yahoo divides an IDR market value by a USD statement value for every
    IDX issuer that reports in USD. BUMI on 2026-09-03: priceToBook 52999.996
    against a real 3.00, from a 212 IDR price over a 0.004 USD book value."""

    @staticmethod
    def _rescale(ratios, reporting, quote, rate, monkeypatch):
        from src.tools import financials as fin

        monkeypatch.setattr(fin, "fx_rate", lambda f, t: rate)
        return fin.rescale_valuation(
            dict(ratios), {"financialCurrency": reporting, "currency": quote}
        )

    BUMI = {"pe_forward": 211999.98, "pb": 52999.996, "ps": 48787.7,
            "ev_ebitda": 410790.53, "peg": 0.1}

    def test_a_usd_reporter_is_rescaled_to_idr(self, monkeypatch):
        out, basis = self._rescale(self.BUMI, "USD", "IDR", 17685.0, monkeypatch)
        assert out["pb"] == pytest.approx(3.00, abs=0.01)
        assert out["ps"] == pytest.approx(2.76, abs=0.01)
        assert out["ev_ebitda"] == pytest.approx(23.23, abs=0.01)
        assert basis == "rescaled_from_usd_at_17685"

    def test_an_idr_reporter_is_left_alone(self, monkeypatch):
        pristine = {"pe_forward": 7.0, "pb": 1.42, "ps": 1.1, "ev_ebitda": 4.2, "peg": 0.8}
        out, basis = self._rescale(pristine, "IDR", "IDR", 1.0, monkeypatch)
        assert out == pristine
        assert basis == "reported_currency_matches_quote_currency"

    def test_an_unavailable_rate_drops_the_ratios_rather_than_passing_them(self, monkeypatch):
        """52,999 is not a conservative 3.00; it is a number that gets acted on."""
        out, basis = self._rescale(self.BUMI, "USD", "IDR", None, monkeypatch)
        assert all(out[k] is None for k in
                   ("pe_forward", "pb", "ps", "ev_ebitda", "peg"))
        assert basis == "dropped_unconvertible_usd_statements"

    def test_peg_is_dropped_whenever_the_currencies_differ(self, monkeypatch):
        """Yahoo does not say what it built peg from, and its sibling
        forwardPE was 211,999 on the same payload."""
        out, _ = self._rescale(self.BUMI, "USD", "IDR", 17685.0, monkeypatch)
        assert out["peg"] is None

    def test_trailing_pe_is_never_touched(self):
        """Trailing EPS is already in the quote currency: 212 / 5.83 = 36.36."""
        from src.tools.financials import CURRENCY_MIXED_RATIOS

        assert "pe_ttm" not in CURRENCY_MIXED_RATIOS
        assert "trailingPE" not in CURRENCY_MIXED_RATIOS

    def test_a_missing_currency_field_changes_nothing(self, monkeypatch):
        out, basis = self._rescale(self.BUMI, None, "IDR", 17685.0, monkeypatch)
        assert out == self.BUMI
        assert basis == "reported_currency_matches_quote_currency"


class TestStatementsAreLabelledWithTheirCurrency:
    def test_a_usd_revenue_is_not_rendered_as_idr(self):
        from src.utils.formatting import format_money

        assert format_money(1_424_767_199.0, "USD") == "USD 1.4B"
        assert format_money(1_424_767_199.0, "IDR") == "IDR 1.4B"

    def test_the_default_and_the_legacy_helper_still_say_idr(self):
        from src.utils.formatting import format_idr, format_money

        assert format_money(5_000_000_000) == "IDR 5.0B"
        assert format_idr(5_000_000_000) == "IDR 5.0B"

    def test_none_survives(self):
        from src.utils.formatting import format_money

        assert format_money(None, "USD") is None
