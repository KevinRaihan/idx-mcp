"""Live integration tests against the real upstream data providers.

These make actual network calls to Yahoo Finance and Google News. They assert on
invariants that must hold for any trading day rather than on specific prices, so
they stay valid as the market moves. Skip with ``pytest -m "not network"``.
"""

import json
import subprocess
import sys
import threading
import queue
from pathlib import Path

import pytest

from src.tools.golden_cross import scan_golden_cross
from src.tools.mean_reversion import scan_mean_reversion
from src.tools.predictions import gather_intelligence
from src.tools.price import get_stock_price
from src.tools.scanner import _download_batch, _load_tickers, _to_jk, scan_ma_breakout
from src.tools.technicals import get_technicals
from src.tools.vol_squeeze import scan_volatility_squeeze
from src.tools.volume_accumulation import scan_volume_accumulation

pytestmark = pytest.mark.network

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LIQUID = ["BBCA", "BBRI", "TLKM", "ASII", "BMRI"]


# ── data layer ────────────────────────────────────────────────────────────────

def test_live_download_never_yields_a_nan_close_on_the_last_bar():
    """The bug that silently zeroed every scan: Yahoo's in-progress session bar."""
    data = _download_batch([_to_jk(t) for t in LIQUID], period="3mo")
    assert len(data) >= 4, f"expected most of {LIQUID} to resolve, got {list(data)}"

    for ticker, df in data.items():
        assert not df["Close"].isna().any(), f"{ticker} still carries NaN closes"
        last = df.iloc[-1]
        assert last["Close"] > 0
        assert last["High"] >= last["Low"]
        assert last["High"] >= last["Close"] >= last["Low"]


def test_live_download_returns_the_expected_columns_and_depth():
    data = _download_batch([_to_jk(t) for t in LIQUID], period="1y")
    for ticker, df in data.items():
        assert {"Open", "High", "Low", "Close", "Volume"} <= set(df.columns)
        assert len(df) > 180, f"{ticker}: only {len(df)} bars for a 1y request"
        assert df.index.is_monotonic_increasing


def test_ticker_universe_is_deduplicated():
    tickers = _load_tickers()
    assert len(tickers) == len(set(tickers))
    assert len(tickers) > 150


# ── single-ticker tools ───────────────────────────────────────────────────────

async def test_live_price_is_plausible():
    r = await get_stock_price("BBCA")
    assert not r.get("error"), r
    assert r["ticker"] == "BBCA"
    assert r["price"] > 0
    assert r["week_52_low"] <= r["price"] <= r["week_52_high"]
    assert r["market_status"] in {"open", "closed", "lunch_break", "pre_open"}


async def test_live_technicals_are_internally_consistent():
    r = await get_technicals("BBCA", "6mo")
    assert not r.get("error"), r

    rsi = r["momentum"]["rsi_14"]
    assert 0 <= rsi <= 100

    macd = r["momentum"]["macd"]
    assert macd["histogram"] == pytest.approx(macd["macd_line"] - macd["signal_line"], abs=0.05)

    stoch = r["momentum"]["stochastic"]
    assert 0 <= stoch["k"] <= 100 and 0 <= stoch["d"] <= 100

    ma = r["moving_averages"]
    assert not (ma["golden_cross"] and ma["death_cross"])


async def test_live_gather_intelligence_returns_both_legs():
    r = await gather_intelligence("BBCA", lookback_days=30, max_articles=3)

    setup = r["trade_setup"]
    assert not setup.get("error"), setup
    assert setup["current_price"] > 0
    assert setup["atr_14"] > 0
    assert setup["support_barrier"] < setup["current_price"] < setup["resistance_barrier"]
    assert 0 < len(setup["ohlcv_data"]) <= 30

    for bar in setup["ohlcv_data"]:
        assert bar["high"] >= bar["low"]
        assert bar["close"] > 0
        assert len(bar["date"]) == 10

    news = r["news_catalysts"]
    assert not news.get("error"), news
    for article in news["articles"]:
        assert article["title"], "an article came back with no title"
        assert article["source"] in {"yfinance", "google_news"}


async def test_a_failing_leg_does_not_take_the_other_down(monkeypatch):
    import src.tools.predictions as preds

    async def broken_news(*a, **k):
        raise RuntimeError("news provider down")

    monkeypatch.setattr(preds, "fetch_idx_news", broken_news)
    r = await preds.gather_intelligence("BBCA", 10, 3)

    assert r["news_catalysts"]["error"] is True
    assert r["trade_setup"]["current_price"] > 0


# ── market-wide scans ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("scan", [
    scan_mean_reversion, scan_volatility_squeeze, scan_volume_accumulation,
])
async def test_live_scans_cover_the_universe_and_report_honest_counts(scan):
    r = await scan()
    assert not r.get("error"), r

    assert r["universe_size"] == len(_load_tickers())
    # A near-total download failure means the scan is broken, not that the
    # market is quiet — that distinction was invisible before.
    assert r["tickers_with_data"] > r["universe_size"] * 0.8
    assert r["tickers_with_data"] + r["tickers_without_data"] == r["universe_size"]
    assert r["signals_found"] == len(r["top_10"]) or r["signals_found"] > len(r["top_10"])
    assert len(r["top_10"]) <= 10
    assert r["elapsed_seconds"] > 0
    assert r["disclaimer"]

    for sig in r["top_10"]:
        assert sig["ticker"].isalnum()
        assert sig["close"] > 0
        assert 0 < sig["confidence_score"] <= 100

    scores = [s["confidence_score"] for s in r["top_10"]]
    assert scores == sorted(scores, reverse=True)


async def test_live_mean_reversion_signals_actually_satisfy_the_filters():
    r = await scan_mean_reversion(rsi_threshold=35.0, min_volume=100_000)
    assert not r.get("error"), r
    for sig in r["top_10"]:
        assert sig["rsi"] < 35.0
        assert sig["distance_below_sma20_pct"] > 5.0
        assert sig["volume"] >= 100_000


async def test_live_volume_accumulation_is_parameter_sensitive():
    """Regression: this scan was structurally incapable of returning a signal."""
    strict = await scan_volume_accumulation()
    loose = await scan_volume_accumulation(
        min_volume=200_000, vol_multiple=1.5, max_spread_pct=25.0
    )
    assert not strict.get("error") and not loose.get("error")
    assert loose["signals_found"] >= strict["signals_found"]
    assert loose["signals_found"] > 0, "no stock in the universe traded above its average volume"

    for sig in loose["top_10"]:
        assert sig["volume_ratio"] >= 1.5
        assert sig["intraday_spread_pct"] <= 25.0
        assert sig["close"] >= sig["prev_close"]


async def test_live_legacy_scans_still_run():
    ma = await scan_ma_breakout()
    assert not ma.get("error"), ma
    assert ma["meta"]["total_tickers_scanned"] > 100

    gc = await scan_golden_cross()
    assert not gc.get("error"), gc
    assert gc["meta"]["total_tickers_scanned"] > 100
    for sig in gc.get("all_signals", []):
        assert sig["sma50"] > sig["sma200"], f"{sig['ticker']} is not in a golden cross"
        assert sig["stoch_k"] <= 25.0


# ── protocol level ────────────────────────────────────────────────────────────

def _rpc_session(requests, timeout=240):
    """Drive the real server binary over stdio and collect the responses."""
    proc = subprocess.Popen(
        [sys.executable, str(PROJECT_ROOT / "run_server.py")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, cwd=str(PROJECT_ROOT),
    )
    lines: queue.Queue[str] = queue.Queue()
    threading.Thread(target=lambda: [lines.put(x) for x in proc.stdout], daemon=True).start()
    stderr: list[str] = []
    threading.Thread(target=lambda: [stderr.append(x) for x in proc.stderr], daemon=True).start()

    def send(obj):
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    def await_id(want):
        while True:
            try:
                msg = json.loads(lines.get(timeout=timeout))
            except json.JSONDecodeError:
                continue
            if msg.get("id") == want:
                return msg

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "1"}}})
        init = await_id(1)
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})

        results = []
        for i, req in enumerate(requests, start=2):
            send({"jsonrpc": "2.0", "id": i, **req})
            results.append(await_id(i))
        return init, results, "".join(stderr)
    finally:
        proc.terminate()
        proc.wait(timeout=15)


def test_server_completes_a_real_stdio_session():
    init, (tools, price), stderr = _rpc_session([
        {"method": "tools/list"},
        {"method": "tools/call", "params": {
            "name": "get_stock_price", "arguments": {"ticker": "BBCA"}}},
    ])

    assert init["result"]["serverInfo"]["name"] == "idx-mcp"
    assert init["result"]["serverInfo"]["version"] == "1.1.0"

    listed = tools["result"]["tools"]
    assert len(listed) == 21
    assert {t["name"] for t in listed} >= {
        "get_stock_price", "scan_mean_reversion",
        "scan_volatility_squeeze", "scan_volume_accumulation",
    }

    payload = json.loads(price["result"]["content"][0]["text"])
    assert payload["ticker"] == "BBCA"
    assert payload["price"] > 0
    assert "Traceback" not in stderr


def test_server_reports_bad_arguments_over_the_wire():
    _, (bad,), _ = _rpc_session([
        {"method": "tools/call", "params": {
            "name": "evaluate_and_log_thesis", "arguments": {"ticker": "BBCA"}}},
    ])
    payload = json.loads(bad["result"]["content"][0]["text"])
    assert payload["error"] is True
    assert payload["error_type"] == "invalid_arguments"


def test_server_exits_loudly_when_dependencies_are_missing(tmp_path):
    """A broken install must fail at boot, not advertise 21 dead tools."""
    stub = tmp_path / "yfinance.py"
    stub.write_text("raise ImportError('simulated broken install')\n", encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "run_server.py")],
        input="", capture_output=True, text=True, timeout=90,
        env={**__import__("os").environ, "PYTHONPATH": str(tmp_path)},
        cwd=str(PROJECT_ROOT),
    )
    assert proc.returncode == 1
    assert "install is incomplete" in proc.stderr
    assert "simulated broken install" in proc.stderr
