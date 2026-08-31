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

from src import server

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
    assert r["market_status"] in {"open", "closed", "lunch_break", "pre_open"}

    # Yahoo's quote endpoint is separately rate-limited from the chart endpoint,
    # so the 52-week range can legitimately be absent. That must be declared
    # rather than left as a silent null next to a healthy-looking price.
    if r["partial"]:
        assert r["missing_fields"]
        assert all(r[k] is None for k in r["missing_fields"])
        assert r["partial_reason"]
    else:
        assert r["week_52_low"] <= r["price"] <= r["week_52_high"]


async def test_live_price_declares_whether_the_quote_was_complete():
    r = await get_stock_price("BBRI")
    assert not r.get("error"), r
    assert isinstance(r["partial"], bool)
    # `partial` and `missing_fields` must agree with each other.
    assert r["partial"] == ("missing_fields" in r)
    if r["partial"]:
        assert all(r[k] is None for k in r["missing_fields"])


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
    # Pinning the literal here went stale on every version bump and only failed
    # in the live suite, long after the unit tests had gone green. What matters
    # is that the running server reports the version the package declares.
    assert init["result"]["serverInfo"]["version"] == server.__version__

    listed = tools["result"]["tools"]
    assert len(listed) == len(server.TOOLS)
    assert {t["name"] for t in listed} >= {
        "get_stock_price", "scan_mean_reversion",
        "scan_volatility_squeeze", "scan_volume_accumulation",
        "scan_relative_strength", "scan_trend_pullback", "scan_breakout_high",
        "scan_distribution_warning", "scan_gap",
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


# ── v1.2 scanners ─────────────────────────────────────────────────────────────

from src.tools.breakout_high import scan_breakout_high          # noqa: E402
from src.tools.distribution import scan_distribution_warning    # noqa: E402
from src.tools.gap import scan_gap                              # noqa: E402
from src.tools.relative_strength import scan_relative_strength  # noqa: E402
from src.tools.trend_pullback import scan_trend_pullback        # noqa: E402
from src.tools import universe as _universe                     # noqa: E402


@pytest.mark.parametrize("scan", [
    scan_relative_strength, scan_trend_pullback, scan_breakout_high,
    scan_distribution_warning, scan_gap,
])
async def test_live_v12_scans_return_a_well_formed_envelope(scan):
    r = await scan()
    assert not r.get("error"), r

    assert r["universe_size"] == len(_load_tickers())
    assert r["tickers_with_data"] > r["universe_size"] * 0.8
    assert r["tickers_with_data"] + r["tickers_without_data"] == r["universe_size"]
    assert len(r["top_10"]) <= 10
    assert r["elapsed_seconds"] > 0
    assert r["disclaimer"]

    for sig in r["top_10"]:
        assert sig["ticker"].isalnum()
        assert sig["close"] > 0
        assert 0 < sig["confidence_score"] <= 100

    scores = [s["confidence_score"] for s in r["top_10"]]
    assert scores == sorted(scores, reverse=True)


async def test_live_relative_strength_signals_actually_beat_the_index():
    r = await scan_relative_strength(min_excess_3m_pct=5.0)
    assert not r.get("error"), r
    assert r["filters_applied"]["benchmark"] == "^JKSE"

    for sig in r["top_10"]:
        assert sig["excess_3m_pct"] >= 5.0
        # Excess return must reconcile with its two components.
        assert sig["excess_3m_pct"] == pytest.approx(
            sig["return_3m_pct"] - sig["ihsg_return_3m_pct"], abs=0.02
        )


async def test_live_relative_strength_rs_high_filter_narrows_the_result():
    lenient = await scan_relative_strength(require_rs_high=False)
    strict = await scan_relative_strength(require_rs_high=True)
    assert strict["signals_found"] <= lenient["signals_found"]
    for sig in strict["top_10"]:
        assert sig["rs_at_3mo_high"] is True


async def test_live_trend_pullback_signals_are_in_confirmed_uptrends():
    r = await scan_trend_pullback()
    assert not r.get("error"), r
    for sig in r["top_10"]:
        assert sig["close"] > sig["sma200"], f"{sig['ticker']} is not above its SMA200"
        assert sig["sma50"] > sig["sma200"]
        assert sig["close"] <= sig["sma20"], f"{sig['ticker']} has not pulled back"
        assert sig["structure_intact"] is True
        assert 40.0 <= sig["rsi"] <= 58.0


async def test_live_breakout_signals_cleared_their_base_on_volume():
    r = await scan_breakout_high()
    assert not r.get("error"), r
    for sig in r["top_10"]:
        assert sig["close"] >= sig["prior_high"]
        assert sig["volume_ratio"] >= 1.5
        assert sig["base_range_pct"] <= 25.0
        assert sig["prior_high"] > sig["prior_low"]


async def test_live_distribution_signals_carry_their_reasons():
    r = await scan_distribution_warning()
    assert not r.get("error"), r
    assert r["interpretation"]
    for sig in r["top_10"]:
        assert sig["warnings"], f"{sig['ticker']} scored without any warning flag"
        assert sig["warning_count"] == len(sig["warnings"])
        assert sig["confidence_score"] >= 50.0


async def test_live_gap_signals_match_their_declared_direction():
    up = await scan_gap(direction="up")
    assert not up.get("error"), up
    for sig in up["top_10"]:
        assert sig["gap_direction"] == "up"
        assert sig["gap_pct"] >= 2.0
        assert sig["close"] > sig["prev_close"]

    down = await scan_gap(direction="down")
    assert not down.get("error"), down
    for sig in down["top_10"]:
        assert sig["gap_direction"] == "down"
        assert sig["gap_pct"] <= -2.0
        assert sig["close"] > sig["open"], "a gap-down signal must be a reversal candle"


# ── the shared universe, under real network conditions ────────────────────────

async def test_all_ten_live_scans_share_one_universe_download(monkeypatch):
    """The structural claim: ten scanners cost one fetch of the universe, not ten."""
    from src.tools.golden_cross import scan_golden_cross as _gc
    from src.tools.scanner import scan_ma_breakout as _ma

    calls: list[int] = []
    real = _universe._download_batch

    def counting(jk_tickers, period="1y", min_rows=20):
        calls.append(len(jk_tickers))
        return real(jk_tickers, period=period, min_rows=min_rows)

    monkeypatch.setattr(_universe, "_download_batch", counting)
    _universe.invalidate_universe()

    for scan in (_ma, _gc, scan_mean_reversion, scan_volatility_squeeze,
                 scan_volume_accumulation, scan_relative_strength,
                 scan_trend_pullback, scan_breakout_high,
                 scan_distribution_warning, scan_gap):
        r = await scan()
        assert not r.get("error"), f"{scan.__name__}: {r}"

    universe = len(_load_tickers())
    batches = -(-universe // _universe.BATCH_SIZE)   # ceil
    assert len(calls) == batches, (
        f"10 scans triggered {len(calls)} download batches; "
        f"one pass over the universe is {batches}"
    )
    assert sum(calls) == universe


# ── all_signals and the filter funnel, across every scan ──────────────────────

ENVELOPE_SCANS = [
    scan_mean_reversion, scan_volatility_squeeze, scan_volume_accumulation,
    scan_relative_strength, scan_trend_pullback, scan_breakout_high,
    scan_distribution_warning, scan_gap,
]


def _funnel_of(result):
    """Envelope scans expose the funnel at the top level; legacy ones under meta."""
    return result.get("filter_funnel") or result.get("meta", {}).get("filter_funnel")


@pytest.mark.parametrize("scan", ENVELOPE_SCANS)
async def test_live_all_signals_is_not_truncated(scan):
    """The bug: signals_found reported 134 while the payload carried 10 rows."""
    r = await scan()
    assert not r.get("error"), r

    assert len(r["all_signals"]) == r["signals_found"]
    assert len(r["top_10"]) == min(10, r["signals_found"])
    # top_10 must be the head of all_signals, not a differently-sorted subset.
    assert [s["ticker"] for s in r["top_10"]] == \
           [s["ticker"] for s in r["all_signals"][:len(r["top_10"])]]


@pytest.mark.parametrize("scan", ENVELOPE_SCANS)
async def test_live_funnel_is_monotonic_and_lands_on_signals_found(scan):
    r = await scan()
    assert not r.get("error"), r

    funnel = _funnel_of(r)
    assert funnel, f"{scan.__name__} returned no filter_funnel"

    counts = list(funnel.values())
    for stage, (a, b) in zip(list(funnel)[1:], zip(counts, counts[1:])):
        assert b <= a, f"{scan.__name__}: stage {stage} ({b}) exceeds the one above it ({a})"

    # The last stage is the final gate, so its survivors are exactly the signals.
    assert counts[-1] == r["signals_found"], (
        f"{scan.__name__}: funnel ends at {counts[-1]} but reports "
        f"{r['signals_found']} signals"
    )
    assert counts[0] <= r["tickers_with_data"]


async def test_live_legacy_scans_also_report_a_funnel():
    from src.tools.golden_cross import scan_golden_cross as _gc
    from src.tools.scanner import scan_ma_breakout as _ma

    for scan in (_ma, _gc):
        r = await scan()
        funnel = _funnel_of(r)
        assert funnel, f"{scan.__name__} returned no filter_funnel"
        counts = list(funnel.values())
        assert counts == sorted(counts, reverse=True)
        assert counts[-1] == r["meta"]["total_signals_found"]


async def test_live_distribution_all_signals_answers_the_veto_question():
    """The real use: is a candidate flagged, even outside the top 10?"""
    r = await scan_distribution_warning(min_warning_score=0.0)
    assert not r.get("error"), r
    assert r["signals_found"] > 10, "expected a broad flag list to test truncation against"

    flagged = {s["ticker"] for s in r["all_signals"]}
    top = {s["ticker"] for s in r["top_10"]}
    assert top < flagged, "all_signals adds nothing beyond top_10"
    for sig in r["all_signals"]:
        assert sig["warnings"]
