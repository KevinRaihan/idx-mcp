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

    def test_a_missing_currency_field_is_not_reported_as_a_match(self, monkeypatch):
        """Unknown is not a match. A rate-limited info call used to land here and
        pass the BUMI mix through under 'reported_currency_matches_quote_currency'."""
        out, basis = self._rescale(self.BUMI, None, "IDR", 17685.0, monkeypatch)
        assert basis == "currency_unverified"
        assert all(out[k] is None for k in ("pe_forward", "pb", "ps", "ev_ebitda"))

    def test_plausible_ratios_survive_an_unknown_currency(self, monkeypatch):
        pristine = {"pe_forward": 7.0, "pb": 1.42, "ps": 1.1, "ev_ebitda": 4.2, "peg": 0.8}
        out, basis = self._rescale(pristine, "IDR", None, 1.0, monkeypatch)
        assert out == pristine
        assert basis == "currency_unverified"


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


class TestGrowthComparesTheSamePeriodAYearEarlier:
    """get_financials(period="quarterly") compared against columns[1], the
    previous quarter, and called it YoY. DMAS Q2-2026 came back as revenue
    -28.77% and net income -54.3% when the year-on-year figures were +611.29%
    and +382.17%. Yahoo also skips quarters, so position cannot be trusted."""

    # Yahoo's DMAS quarterly columns on 2026-09-13: no Sep-25.
    DMAS_COLS = ["2026-06-30", "2026-03-31", "2025-12-31", "2025-06-30", "2025-03-31"]
    DMAS_REV = [750216406794.0, 1053230360920.0, 529508075971.0, 105472691544.0, 507885095770.0]
    DMAS_NI = [373986015787.0, 818266564302.0, 275166937745.0, 77563373165.0, 355452713865.0]

    @staticmethod
    def _stmt(cols, rev, ni):
        import pandas as pd

        return pd.DataFrame(
            {pd.Timestamp(c): {"Total Revenue": r, "Net Income": n}
             for c, r, n in zip(cols, rev, ni)}
        )

    async def _payload(self, monkeypatch, cols, rev, ni):
        info = {"trailingEps": 32.37, "netIncomeToCommon": 1_557_000_000_000.0,
                "currency": "IDR", "financialCurrency": "IDR"}
        out = await _financials_from(monkeypatch, info, self._stmt(cols, rev, ni), "quarterly")
        return out["income_statement"]

    def test_dmas_q2_is_compared_with_q2_not_q1(self):
        from src.tools.financials import yoy_growth

        rev, ni, prior = yoy_growth(self._stmt(self.DMAS_COLS, self.DMAS_REV, self.DMAS_NI))
        assert prior == "2025-06-30"
        assert rev == pytest.approx(611.29, abs=0.01)
        assert ni == pytest.approx(382.17, abs=0.01)

    def test_the_published_numbers_were_quarter_on_quarter(self):
        """Teeth: the figures the tool used to publish are exactly Q2 over Q1."""
        rev, ni = self.DMAS_REV, self.DMAS_NI
        assert round((rev[0] - rev[1]) / rev[1] * 100, 2) == -28.77
        assert round((ni[0] - ni[1]) / ni[1] * 100, 2) == -54.3

    def test_a_skipped_quarter_does_not_shift_the_comparison(self):
        """No Sep-25 column, so a fixed offset of four lands on Mar-25."""
        import pandas as pd

        from src.tools.financials import same_period_last_year

        cols = [pd.Timestamp(c) for c in self.DMAS_COLS]
        assert same_period_last_year(cols[1:], cols[0]) == pd.Timestamp("2025-06-30")
        assert cols[4] == pd.Timestamp("2025-03-31")

    def test_a_missing_year_ago_period_yields_none_not_a_neighbour(self):
        from src.tools.financials import yoy_growth

        cols = ["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30"]
        assert yoy_growth(self._stmt(cols, [1.0] * 4, [1.0] * 4)) == (None, None, None)

    def test_adjacent_quarters_fall_outside_the_tolerance(self):
        """Jun-26 against Mar-25 is 456 days and against Sep-25 is 273: both
        sit about 91 days from a year, so the tolerance must stay below that."""
        from src.tools.financials import SAME_PERIOD_TOLERANCE_DAYS

        assert 1 <= SAME_PERIOD_TOLERANCE_DAYS < 91

    @pytest.mark.parametrize("latest, prior", [
        ("2024-06-30", "2023-06-30"),   # 366 days, leap year
        ("2024-12-31", "2023-12-31"),   # 366 days, leap year
        ("2025-12-31", "2024-12-31"),   # annual columns
    ])
    def test_leap_years_and_annual_columns_still_match(self, latest, prior):
        from src.tools.financials import yoy_growth

        rev, ni, found = yoy_growth(self._stmt([latest, prior], [110.0, 100.0], [55.0, 50.0]))
        assert (rev, ni, found) == (10.0, 10.0, prior)

    def test_a_single_column_has_no_growth(self):
        from src.tools.financials import yoy_growth

        assert yoy_growth(self._stmt(["2026-06-30"], [1.0], [1.0])) == (None, None, None)

    async def test_the_payload_says_what_it_compared_and_what_eps_is(self, monkeypatch):
        inc = await self._payload(monkeypatch, self.DMAS_COLS, self.DMAS_REV, self.DMAS_NI)
        assert inc["revenue_growth_yoy_pct"] == pytest.approx(611.29, abs=0.01)
        assert inc["net_income_growth_yoy_pct"] == pytest.approx(382.17, abs=0.01)
        assert inc["growth_basis"] == "same_period_prior_year"
        assert inc["growth_compared_with_period_ending"] == "2025-06-30"
        assert inc["eps"] == 32.37
        assert inc["eps_basis"] == "trailing_twelve_months"

    async def test_the_payload_says_when_the_year_ago_period_is_missing(self, monkeypatch):
        cols = ["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30"]
        inc = await self._payload(monkeypatch, cols, [4.0, 3.0, 2.0, 1.0], [4.0, 3.0, 2.0, 1.0])
        assert inc["revenue_growth_yoy_pct"] is None
        assert inc["net_income_growth_yoy_pct"] is None
        assert inc["growth_basis"] == "prior_year_period_missing_from_source"
        assert inc["growth_compared_with_period_ending"] is None


async def _financials_from(monkeypatch, info, stmt, period="annual", ticker="TEST"):
    """get_financials against a canned Yahoo response, with the cache bypassed."""
    import pandas as pd

    from src.tools import financials as fin

    prefix = "quarterly_" if period == "quarterly" else ""
    fake = type("FakeTicker", (), {
        "info": info,
        f"{prefix}financials": stmt,
        f"{prefix}balance_sheet": pd.DataFrame(),
        f"{prefix}cashflow": pd.DataFrame(),
    })()
    monkeypatch.setattr(fin.yf, "Ticker", lambda symbol: fake)
    monkeypatch.setattr(fin.cache, "get", lambda *a, **k: None)
    monkeypatch.setattr(fin.cache, "set", lambda *a, **k: None)
    return await fin.get_financials(ticker, period=period)


class TestEarningsThatContradictThemselvesAreDropped:
    """COCO on 2026-09-13: trailingEps 7.275 and trailingPE 18.0 while
    netIncomeToCommon was IDR -198.6B, a loss larger than its revenue. The P/E
    priced a profit the company no longer makes."""

    COCO = {"currency": "IDR", "financialCurrency": "IDR", "trailingEps": 7.275,
            "trailingPE": 18.006872, "netIncomeToCommon": -198559924224.0}
    BBCA = {"currency": "IDR", "financialCurrency": "IDR", "trailingEps": 471.84,
            "trailingPE": 13.404968, "netIncomeToCommon": 58055320403968.0}
    # USD net income beside IDR EPS: magnitudes differ by the FX rate, signs do not.
    BUMI = {"currency": "IDR", "financialCurrency": "USD", "trailingEps": 5.75,
            "trailingPE": 36.869564, "netIncomeToCommon": 119456304.0}

    DROPPED = "dropped_trailing_eps_contradicts_ttm_net_income"

    def test_a_stale_profit_beside_a_loss_is_dropped(self):
        from src.tools.financials import trailing_earnings

        assert trailing_earnings(self.COCO) == (None, None, self.DROPPED)

    def test_consistent_earnings_pass_through(self):
        from src.tools.financials import trailing_earnings

        assert trailing_earnings(self.BBCA) == (471.84, 13.404968, "trailing_twelve_months")

    def test_the_check_survives_a_currency_mismatch(self):
        from src.tools.financials import trailing_earnings

        assert trailing_earnings(self.BUMI) == (5.75, 36.869564, "trailing_twelve_months")

    def test_a_stale_loss_beside_a_profit_is_dropped_too(self):
        from src.tools.financials import trailing_earnings

        info = dict(self.BBCA, trailingEps=-4.0, trailingPE=None)
        assert trailing_earnings(info) == (None, None, self.DROPPED)

    def test_a_consistent_loss_keeps_its_eps_but_has_no_pe(self):
        from src.tools.financials import trailing_earnings

        info = dict(self.COCO, trailingEps=-13.95, trailingPE=9.4)
        assert trailing_earnings(info) == (-13.95, None, "trailing_twelve_months_no_pe_for_a_loss")

    def test_without_net_income_the_eps_is_marked_unchecked(self):
        from src.tools.financials import trailing_earnings

        info = {k: v for k, v in self.BBCA.items() if k != "netIncomeToCommon"}
        assert trailing_earnings(info) == (471.84, 13.404968, "trailing_twelve_months_unchecked")

    def test_a_pe_without_an_eps_cannot_be_checked(self):
        from src.tools.financials import trailing_earnings

        assert trailing_earnings({"trailingPE": 18.0}) == (None, None, None)

    async def test_the_payload_prints_no_pe_for_coco(self, monkeypatch):
        import pandas as pd

        out = await _financials_from(monkeypatch, self.COCO, pd.DataFrame(), ticker="COCO")
        assert out["valuation"]["pe_ttm"] is None
        assert out["income_statement"]["eps"] is None
        assert out["valuation"]["pe_ttm_basis"] == self.DROPPED
        assert out["income_statement"]["eps_basis"] == self.DROPPED


class TestAnUnknownCurrencyIsNotReportedAsIdr:
    """When Yahoo rate-limits the info call (HTTP 429, then 401 'Invalid
    Crumb'), financialCurrency is missing. BUMI's USD 449.1M quarterly revenue
    printed as 'IDR 449.1M' and valuation_basis claimed the currencies matched."""

    def test_an_unknown_currency_gets_no_label(self):
        from src.utils.formatting import format_money

        assert format_money(449_094_722.0, None) == "449.1M"
        assert format_money(-2_000_000_000_000.0, None) == "-2.0T"
        assert format_money(950.0, None) == "950"

    async def test_a_rate_limited_info_call_is_reported_as_unknown(self, monkeypatch):
        import pandas as pd

        stmt = pd.DataFrame({pd.Timestamp("2026-06-30"):
                             {"Total Revenue": 449094722.0, "Net Income": 36292676.0}})
        out = await _financials_from(monkeypatch, {}, stmt, "quarterly", ticker="BUMI")
        inc = out["income_statement"]
        assert inc["reporting_currency"] is None
        assert inc["revenue_formatted"] == "449.1M"
        assert inc["net_income_formatted"] == "36.3M"
        assert out["valuation"]["valuation_basis"] == "currency_unverified"
        assert out["partial"] is True
        assert "income_statement.reporting_currency" in out["missing_fields"]
