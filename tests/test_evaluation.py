"""Forward-test scoring: resolving a logged thesis against its price history.

Bars are synthesised so each outcome branch can be driven exactly, including
the two cases that decide whether a forward test is honest — a session that
touches both the target and the stop, and a thesis scored from the session
after it was logged rather than the one it was logged on.
"""

import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from src.tools import evaluation as ev
from src.tools.predictions import log_prediction_snapshot
from src.utils.paths import predictions_log_file

LEVELS = dict(entry_price=1_000.0, stop_loss=950.0, target_price=1_100.0)


def bars(rows, start="2026-08-03"):
    """rows: list of (high, low, close)."""
    idx = pd.bdate_range(start=start, periods=len(rows))
    return pd.DataFrame(
        {
            "Open": [r[2] for r in rows],
            "High": [r[0] for r in rows],
            "Low": [r[1] for r in rows],
            "Close": [r[2] for r in rows],
            "Volume": [1_000_000] * len(rows),
        },
        index=idx,
    )


def thesis(**over):
    base = {
        "ticker": "BBCA", "entry_price": 1_000.0, "stop_loss": 950.0,
        "target_price": 1_100.0, "direction": "long",
        "target_date": "2026-09-30", "position_value_idr": 10_000_000,
    }
    base.update(over)
    return base


AS_OF = datetime(2026, 8, 20, tzinfo=timezone.utc)


# ── outcome branches ──────────────────────────────────────────────────────────

def test_target_hit_is_scored_as_a_win():
    frame = bars([(1_010, 990, 1_000), (1_120, 1_050, 1_110)])
    r = ev.resolve_outcome(thesis(), frame, AS_OF)
    assert r["outcome"] == "hit_target"
    assert r["exit_price"] == 1_100.0
    assert r["return_pct"] == pytest.approx(10.0)


def test_stop_hit_is_scored_as_a_loss():
    frame = bars([(1_010, 990, 1_000), (1_000, 940, 945)])
    r = ev.resolve_outcome(thesis(), frame, AS_OF)
    assert r["outcome"] == "hit_stop"
    assert r["exit_price"] == 950.0
    assert r["return_pct"] == pytest.approx(-5.0)


def test_whichever_comes_first_wins_across_bars():
    """Stop on day 1, target on day 2 — the stop already ended the trade."""
    frame = bars([(1_000, 940, 960), (1_150, 1_000, 1_140)])
    assert ev.resolve_outcome(thesis(), frame, AS_OF)["outcome"] == "hit_stop"

    frame = bars([(1_150, 1_000, 1_140), (1_000, 940, 960)])
    assert ev.resolve_outcome(thesis(), frame, AS_OF)["outcome"] == "hit_target"


def test_same_bar_touching_both_is_scored_pessimistically_and_flagged():
    """Daily bars cannot order two touches; grading it a win would be cheating."""
    frame = bars([(1_150, 940, 1_050)])
    r = ev.resolve_outcome(thesis(), frame, AS_OF)
    assert r["outcome"] == "hit_stop"
    assert r["same_bar_ambiguous"] is True
    assert "cannot order" in r["note"]


def test_unresolved_before_the_target_date_is_open():
    frame = bars([(1_010, 990, 1_005), (1_020, 995, 1_015)])
    r = ev.resolve_outcome(thesis(), frame, AS_OF)
    assert r["outcome"] == "open"
    assert r["exit_price"] == 1_015.0


def test_unresolved_after_the_target_date_is_expired():
    frame = bars([(1_010, 990, 1_005), (1_020, 995, 1_015)])
    r = ev.resolve_outcome(thesis(), frame, datetime(2026, 10, 5, tzinfo=timezone.utc))
    assert r["outcome"] == "expired"
    assert r["return_pct"] == pytest.approx(1.5)


def test_no_bars_is_reported_rather_than_guessed():
    """Default assumes the fetch worked, so an empty window means nothing has happened yet."""
    r = ev.resolve_outcome(thesis(), pd.DataFrame(), AS_OF)
    assert r["outcome"] == "pending"


def test_short_direction_inverts_the_tests():
    short = thesis(direction="short", stop_loss=1_050.0, target_price=900.0)
    hit = bars([(1_010, 890, 895)])
    assert ev.resolve_outcome(short, hit, AS_OF)["outcome"] == "hit_target"

    stopped = bars([(1_060, 1_000, 1_055)])
    assert ev.resolve_outcome(short, stopped, AS_OF)["outcome"] == "hit_stop"


def test_realized_pnl_is_net_of_both_fee_legs():
    frame = bars([(1_120, 1_050, 1_110)])
    r = ev.resolve_outcome(thesis(position_value_idr=10_000_000), frame, AS_OF)
    # 10,000 shares at 1000; +100 each = 1,000,000 gross, less 0.4% of 10m.
    assert r["fees_idr"] == pytest.approx(40_000)
    assert r["realized_pnl_idr"] == pytest.approx(960_000)


# ── entry timing ──────────────────────────────────────────────────────────────

def test_scoring_starts_the_session_after_the_thesis_was_logged():
    """A thesis logged on today's close could not have been entered today."""
    logged = datetime(2026, 8, 4, 9, 0, tzinfo=timezone.utc)
    frame = bars([(1_150, 900, 1_000)], start="2026-08-03")  # 3rd and 4th
    frame = pd.concat([frame, bars([(1_010, 995, 1_005)], start="2026-08-05")])

    window = ev._bars_after_log(frame, logged)
    assert list(window.index.date) == [datetime(2026, 8, 5).date()]
    # The 4th's wild bar must not decide the trade.
    assert ev.resolve_outcome(thesis(), window, AS_OF)["outcome"] == "open"


def test_bars_after_log_on_an_empty_frame_is_safe():
    assert ev._bars_after_log(pd.DataFrame(), AS_OF).empty


# ── level recovery for pre-v3 records ─────────────────────────────────────────

def test_levels_are_parsed_out_of_prose_reasoning():
    entry = {"reasoning": "ENTRY 1365 STOP 1315 TARGET 1450, 73 lots, R/R 1.70."}
    assert ev._levels_from_reasoning(entry) == (1365.0, 1315.0, 1450.0)


def test_prose_parsing_rejects_transposed_levels():
    entry = {"reasoning": "ENTRY 1365 STOP 1450 TARGET 1315"}
    assert ev._levels_from_reasoning(entry) is None


def test_prose_parsing_returns_none_without_a_match():
    assert ev._levels_from_reasoning({"reasoning": "no levels here"}) is None
    assert ev._levels_from_reasoning({}) is None


def test_levels_are_reconstructed_from_the_idr_ratios():
    """profit/position is the fractional move, so levels follow from entry price."""
    entry = {"position_value_idr": 10_000_000,
             "profit_target_idr": 500_000, "loss_target_idr": 250_000}
    e, s, t = ev._levels_from_ratios(entry, 1_000.0)
    assert (e, s, t) == (1_000.0, 975.0, 1_050.0)


def test_reconstruction_needs_the_idr_magnitudes():
    assert ev._levels_from_ratios({"position_value_idr": 0}, 1_000.0) is None
    assert ev._levels_from_ratios({}, 1_000.0) is None


# ── aggregation ───────────────────────────────────────────────────────────────

def _scored(outcome, prob, ret, strategy="s", pnl=None):
    return {"outcome": outcome, "ai_win_prob": prob, "return_pct": ret,
            "strategy": strategy, "realized_pnl_idr": pnl}


def test_summary_counts_only_decided_trades_in_the_win_rate():
    rows = [
        _scored("hit_target", 0.5, 10.0), _scored("hit_target", 0.5, 10.0),
        _scored("hit_stop", 0.5, -5.0), _scored("hit_stop", 0.5, -5.0),
        _scored("open", 0.5, 1.0), _scored("expired", 0.5, 2.0),
    ]
    s = ev._summarise(rows)
    assert s["decided"] == 4
    assert s["realized_win_rate"] == pytest.approx(0.5)
    assert s["open"] == 1 and s["expired"] == 1
    assert s["logged"] == 6


def test_calibration_gap_is_positive_when_theses_were_optimistic():
    rows = [_scored("hit_target", 0.8, 10.0), _scored("hit_stop", 0.8, -5.0),
            _scored("hit_stop", 0.8, -5.0), _scored("hit_stop", 0.8, -5.0)]
    s = ev._summarise(rows)
    assert s["realized_win_rate"] == pytest.approx(0.25)
    assert s["mean_predicted_win_prob"] == pytest.approx(0.8)
    assert s["calibration_gap"] == pytest.approx(0.55)


def test_calibration_gap_is_absent_without_decided_trades():
    s = ev._summarise([_scored("open", 0.6, 1.0)])
    assert s["realized_win_rate"] is None
    assert "calibration_gap" not in s


def test_per_strategy_breakdown_splits_the_rows():
    rows = [_scored("hit_target", 0.5, 10.0, "alpha"),
            _scored("hit_stop", 0.5, -5.0, "beta"),
            _scored("hit_target", 0.5, 8.0, "beta")]
    by = ev._by_strategy(rows)
    assert set(by) == {"alpha", "beta"}
    assert by["alpha"]["realized_win_rate"] == pytest.approx(1.0)
    assert by["beta"]["realized_win_rate"] == pytest.approx(0.5)


# ── migration ─────────────────────────────────────────────────────────────────

def test_migration_upgrades_a_v2_entry_from_its_prose():
    v2 = [{"ticker": "AKRA", "schema_version": 2, "timestamp": "2026-08-29T12:00:00+00:00",
           "reasoning": "ENTRY 1365 STOP 1315 TARGET 1450.", "target_date": "2026-09-12"}]
    out, stats = ev._migrate_entries(v2)
    assert out[0]["entry_price"] == 1365.0
    assert out[0]["levels_source"] == "parsed_from_reasoning"
    assert out[0]["schema_version"] == ev.SCHEMA_VERSION
    assert stats["parsed_from_reasoning"] == 1


def test_migration_leaves_a_v3_entry_alone():
    v3 = [{"ticker": "BBCA", "entry_price": 1_000.0, "stop_loss": 950.0,
           "target_price": 1_100.0, "levels_source": "declared", "schema_version": 3}]
    out, stats = ev._migrate_entries(v3)
    assert stats["already_v3"] == 1
    assert out[0]["entry_price"] == 1_000.0


def test_migration_marks_an_unrecoverable_entry_rather_than_inventing_levels():
    orphan = [{"ticker": "XXXX", "schema_version": 2, "reasoning": "no numbers"}]
    out, stats = ev._migrate_entries(orphan)
    assert out[0]["levels_source"] == "unresolvable"
    assert "entry_price" not in out[0]
    assert stats["unresolvable"] == 1


# ── the tool end to end ───────────────────────────────────────────────────────

async def test_evaluate_reports_no_predictions_on_an_empty_log():
    r = await ev.evaluate_predictions()
    assert r["error"] is True
    assert r["error_type"] == "no_predictions"


async def test_evaluate_scores_a_logged_thesis(monkeypatch):
    await log_prediction_snapshot(
        "BBCA", 1000.0, 0.6, "r", "2026-09-30", "unit_strategy", **LEVELS
    )

    monkeypatch.setattr(ev, "_fetch_bars",
                        lambda t, s, e: bars([(1_120, 1_050, 1_110)], start="2026-09-01"))
    r = await ev.evaluate_predictions()

    assert r["summary"]["logged"] == 1
    assert r["summary"]["hit_target"] == 1
    assert r["summary"]["realized_win_rate"] == pytest.approx(1.0)
    assert r["by_strategy"]["unit_strategy"]["decided"] == 1
    assert r["predictions"][0]["ticker"] == "BBCA"
    assert r["predictions"][0]["levels_source"] == "declared"


async def test_evaluate_filters_by_strategy(monkeypatch):
    await log_prediction_snapshot("BBCA", 1.0, 0.5, "r", "2026-09-30", "alpha_scan", **LEVELS)
    await log_prediction_snapshot("TLKM", 1.0, 0.5, "r", "2026-09-30", "beta_scan", **LEVELS)

    monkeypatch.setattr(ev, "_fetch_bars",
                        lambda t, s, e: bars([(1_120, 1_050, 1_110)], start="2026-09-01"))
    r = await ev.evaluate_predictions(strategy="alpha")
    assert r["summary"]["logged"] == 1
    assert r["predictions"][0]["ticker"] == "BBCA"


async def test_include_open_false_hides_unresolved_rows_but_not_from_counts(monkeypatch):
    await log_prediction_snapshot("BBCA", 1.0, 0.5, "r", "2099-01-01", "s", **LEVELS)
    monkeypatch.setattr(ev, "_fetch_bars",
                        lambda t, s, e: bars([(1_010, 990, 1_000)], start="2026-09-01"))

    r = await ev.evaluate_predictions(include_open=False)
    assert r["predictions"] == []
    assert r["summary"]["open"] == 1


async def test_evaluate_survives_an_entry_with_no_recoverable_levels(monkeypatch):
    path = predictions_log_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([{
        "ticker": "BBCA", "schema_version": 2, "strategy": "legacy",
        "timestamp": "2026-08-01T00:00:00+00:00", "target_date": "2026-09-01",
        "reasoning": "no levels anywhere", "ai_win_prob": 0.5,
    }]), encoding="utf-8")

    monkeypatch.setattr(ev, "_fetch_bars", lambda t, s, e: None)
    r = await ev.evaluate_predictions()
    assert r["summary"]["unscorable"] == 1
    assert r["predictions"][0]["outcome"] == "no_levels"


def test_no_sessions_yet_is_pending_not_a_data_failure():
    """A thesis logged after the last close has nothing to be judged on yet."""
    r = ev.resolve_outcome(thesis(), pd.DataFrame(), AS_OF, fetch_succeeded=True)
    assert r["outcome"] == "pending"
    assert "nothing to score yet" in r["note"]


def test_a_failed_fetch_stays_no_data():
    r = ev.resolve_outcome(thesis(), pd.DataFrame(), AS_OF, fetch_succeeded=False)
    assert r["outcome"] == "no_data"
    assert "could not be fetched" in r["note"]


def test_summary_counts_pending_apart_from_unscorable():
    rows = [_scored("pending", 0.5, None), _scored("no_data", 0.5, None),
            _scored("no_levels", 0.5, None), _scored("hit_target", 0.5, 10.0)]
    s = ev._summarise(rows)
    assert s["pending"] == 1
    assert s["unscorable"] == 2
    assert s["decided"] == 1


async def test_a_thesis_logged_today_reports_pending(monkeypatch):
    await log_prediction_snapshot("BBCA", 1.0, 0.5, "r", "2099-01-01", "s", **LEVELS)
    # Fetch works, but every bar predates the thesis.
    monkeypatch.setattr(ev, "_fetch_bars",
                        lambda t, s, e: bars([(1_010, 990, 1_000)], start="2020-01-01"))
    r = await ev.evaluate_predictions()
    assert r["summary"]["pending"] == 1
    assert r["summary"]["unscorable"] == 0
    assert r["predictions"][0]["outcome"] == "pending"
