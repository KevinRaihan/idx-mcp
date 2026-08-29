"""Durability of the forward-testing prediction log.

The log is a read-modify-write of a single JSON file. Previously it was written
non-atomically into the package directory, and a single malformed byte made
every subsequent write fail for good.
"""

import asyncio
import json
import os

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
    entry_price=10_000.0,
    stop_loss=9_900.0,
    target_price=10_200.0,
)

# Positional tail shared by the raw log_prediction_snapshot calls below:
# position_value, profit_target, loss_target, entry, stop, target.
LEVELS = dict(entry_price=1_000.0, stop_loss=950.0, target_price=1_100.0)


async def test_snapshot_is_written_under_idx_mcp_home():
    r = await log_prediction_snapshot("BBCA", 1000.0, 0.6, "why", "2026-09-30", "s", **LEVELS)
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
        await log_prediction_snapshot(tk, 1.0, 0.5, "r", "2026-09-30", "s", **LEVELS)

    entries = json.loads(predictions_log_file().read_text(encoding="utf-8"))
    assert [e["ticker"] for e in entries] == ["BBCA", "TLKM", "ASII"]


async def test_corrupt_log_is_quarantined_and_logging_resumes():
    path = predictions_log_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not json", encoding="utf-8")

    r = await log_prediction_snapshot("BBCA", 1.0, 0.5, "r", "2026-09-30", "s", **LEVELS)
    assert r["success"] is True

    entries = json.loads(path.read_text(encoding="utf-8"))
    assert len(entries) == 1
    assert list(path.parent.glob("predictions_log.corrupt-*.json"))


async def test_empty_log_file_is_treated_as_no_entries():
    path = predictions_log_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("   ", encoding="utf-8")

    assert (await log_prediction_snapshot("BBCA", 1.0, 0.5, "r", "2026-09-30", "s", **LEVELS))["success"]
    assert len(json.loads(path.read_text(encoding="utf-8"))) == 1


async def test_concurrent_writes_do_not_lose_entries():
    await asyncio.gather(*[
        log_prediction_snapshot(f"T{i:03d}", float(i), 0.5, "r", "2026-09-30", "s", **LEVELS)
        for i in range(25)
    ])
    entries = json.loads(predictions_log_file().read_text(encoding="utf-8"))
    assert len(entries) == 25
    assert len({e["ticker"] for e in entries}) == 25


async def test_no_temp_file_is_left_behind():
    await log_prediction_snapshot("BBCA", 1.0, 0.5, "r", "2026-09-30", "s", **LEVELS)
    assert not list(predictions_log_file().parent.glob("*.tmp"))


@pytest.mark.parametrize("bad_date", ["30-09-2026", "2026/09/30", "tomorrow", ""])
async def test_malformed_target_date_is_rejected(bad_date):
    r = await log_prediction_snapshot("BBCA", 1.0, 0.5, "r", bad_date, "s", **LEVELS)
    assert r["error"] is True
    assert "YYYY-MM-DD" in r["message"]


async def test_invalid_ticker_is_rejected_before_writing():
    r = await log_prediction_snapshot("NOT A TICKER", 1.0, 0.5, "r", "2026-09-30", "s", **LEVELS)
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
    assert entry["schema_version"] == 3
    assert entry["entry_price"] == 10_000.0
    assert entry["stop_loss"] == 9_900.0
    assert entry["target_price"] == 10_200.0
    assert entry["direction"] == "long"
    assert entry["levels_source"] == "declared"


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


# ── cross-process safety ──────────────────────────────────────────────────────

def test_separate_processes_do_not_lose_entries(tmp_path):
    """The threading lock is per-interpreter; this log has multiple writers.

    A Claude Code session and the Antigravity app each run their own server
    process against the same file. Without an advisory file lock their
    read-append-write cycles interleave and the later write drops whatever the
    earlier one appended.
    """
    import subprocess
    import sys
    from pathlib import Path

    home = tmp_path / "idx-mcp-home"
    root = Path(__file__).resolve().parent.parent

    writer = tmp_path / "writer.py"
    writer.write_text(
        "import asyncio, sys\n"
        f"sys.path.insert(0, {str(root)!r})\n"
        "from src.tools.predictions import log_prediction_snapshot\n"
        "tag = sys.argv[1]\n"
        "async def main():\n"
        "    for i in range(12):\n"
        "        await log_prediction_snapshot(\n"
        "            'BBCA', 1.0, 0.5, f'{tag}-{i}', '2026-09-30', tag,\n"
        "            entry_price=1000.0, stop_loss=950.0, target_price=1100.0)\n"
        "asyncio.run(main())\n",
        encoding="utf-8",
    )

    env = {**os.environ, "IDX_MCP_HOME": str(home), "PYTHONPATH": str(root)}
    procs = [
        subprocess.Popen([sys.executable, str(writer), f"proc{n}"],
                         env=env, cwd=str(root),
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for n in range(4)
    ]
    for p in procs:
        _, err = p.communicate(timeout=120)
        assert p.returncode == 0, err.decode()

    log = home / "logs" / "predictions_log.json"
    entries = json.loads(log.read_text(encoding="utf-8"))

    # 4 processes x 12 appends, none clobbered by an interleaved write.
    assert len(entries) == 48, f"expected 48 entries, found {len(entries)}"
    assert {e["strategy"] for e in entries} == {f"proc{n}" for n in range(4)}
    for n in range(4):
        assert sum(1 for e in entries if e["strategy"] == f"proc{n}") == 12
