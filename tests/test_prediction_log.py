"""Durability of the forward-testing prediction log.

The log is a read-modify-write of a single JSON file. Previously it was written
non-atomically into the package directory, and a single malformed byte made
every subsequent write fail for good.
"""

import asyncio
import json

import pytest

from src.tools.predictions import evaluate_and_log_thesis, log_prediction_snapshot
from src.utils.paths import predictions_log_file

THESIS = dict(
    ticker="BBCA",
    win_prob=0.6,
    profit_target_idr=100_000,
    loss_target_idr=50_000,
    position_value_idr=5_000_000,
    reasoning="unit test thesis",
    target_date="2026-09-30",
    strategy_name="test_strategy",
)


async def test_snapshot_is_written_under_idx_mcp_home():
    r = await log_prediction_snapshot("BBCA", 1000.0, 0.6, "why", "2026-09-30", "s")
    assert r["success"] is True

    path = predictions_log_file()
    assert path.exists()
    # Never inside the installed package.
    assert "site-packages" not in str(path)

    entries = json.loads(path.read_text(encoding="utf-8"))
    assert len(entries) == 1
    assert entries[0]["ticker"] == "BBCA"
    assert entries[0]["strategy"] == "s"


async def test_entries_append_rather_than_overwrite():
    for tk in ["BBCA", "TLKM", "ASII"]:
        await log_prediction_snapshot(tk, 1.0, 0.5, "r", "2026-09-30", "s")

    entries = json.loads(predictions_log_file().read_text(encoding="utf-8"))
    assert [e["ticker"] for e in entries] == ["BBCA", "TLKM", "ASII"]


async def test_corrupt_log_is_quarantined_and_logging_resumes():
    path = predictions_log_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not json", encoding="utf-8")

    r = await log_prediction_snapshot("BBCA", 1.0, 0.5, "r", "2026-09-30", "s")
    assert r["success"] is True

    entries = json.loads(path.read_text(encoding="utf-8"))
    assert len(entries) == 1
    assert list(path.parent.glob("predictions_log.corrupt-*.json"))


async def test_empty_log_file_is_treated_as_no_entries():
    path = predictions_log_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("   ", encoding="utf-8")

    assert (await log_prediction_snapshot("BBCA", 1.0, 0.5, "r", "2026-09-30", "s"))["success"]
    assert len(json.loads(path.read_text(encoding="utf-8"))) == 1


async def test_concurrent_writes_do_not_lose_entries():
    await asyncio.gather(*[
        log_prediction_snapshot(f"T{i:03d}", float(i), 0.5, "r", "2026-09-30", "s")
        for i in range(25)
    ])
    entries = json.loads(predictions_log_file().read_text(encoding="utf-8"))
    assert len(entries) == 25
    assert len({e["ticker"] for e in entries}) == 25


async def test_no_temp_file_is_left_behind():
    await log_prediction_snapshot("BBCA", 1.0, 0.5, "r", "2026-09-30", "s")
    assert not list(predictions_log_file().parent.glob("*.tmp"))


@pytest.mark.parametrize("bad_date", ["30-09-2026", "2026/09/30", "tomorrow", ""])
async def test_malformed_target_date_is_rejected(bad_date):
    r = await log_prediction_snapshot("BBCA", 1.0, 0.5, "r", bad_date, "s")
    assert r["error"] is True
    assert "YYYY-MM-DD" in r["message"]


async def test_invalid_ticker_is_rejected_before_writing():
    r = await log_prediction_snapshot("NOT A TICKER", 1.0, 0.5, "r", "2026-09-30", "s")
    assert r["error"] is True
    assert not predictions_log_file().exists()


async def test_thesis_logs_the_sizing_inputs_alongside_the_ev():
    r = await evaluate_and_log_thesis(**THESIS)
    assert r["ev_verdict"] == "positive_edge"
    assert r["logging_status"]["success"] is True

    entry = json.loads(predictions_log_file().read_text(encoding="utf-8"))[0]
    assert entry["position_value_idr"] == 5_000_000
    assert entry["profit_target_idr"] == 100_000
    assert entry["loss_target_idr"] == 50_000
    assert entry["initial_ev"] == pytest.approx(20_000.0)
    assert entry["schema_version"] == 2


async def test_negative_edge_theses_are_still_recorded():
    """Forward testing needs the rejected trades, not just the taken ones."""
    r = await evaluate_and_log_thesis(**{**THESIS, "win_prob": 0.2})
    assert r["ev_verdict"] == "negative_edge"
    assert r["logging_status"]["success"] is True
    assert len(json.loads(predictions_log_file().read_text(encoding="utf-8"))) == 1


async def test_invalid_ev_input_short_circuits_before_logging():
    r = await evaluate_and_log_thesis(**{**THESIS, "position_value_idr": 0})
    assert r["error"] is True
    assert not predictions_log_file().exists()
