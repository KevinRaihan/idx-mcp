# idx-mcp — IDX Stock Data MCP Server

A local MCP (Model Context Protocol) server that provides Indonesian Stock Exchange (IDX) data to Claude Code and Claude Desktop. Exposes **27 tools** returning structured JSON for stock prices, financials, technicals, broker flow, market-wide strategy scans, and trade-thesis evaluation.

**Version 1.6.0** — unified 2-step workflow, a ten-strategy scanner ensemble over one shared universe fetch, and a forward test that scores its own logged theses.

## Architecture

```
Claude Code / Claude Desktop
        ↓ MCP protocol (stdio transport)
   idx-mcp server (runs locally)
        ↓
   Data Sources:
   ├── yfinance (price, fundamentals, historicals)
   ├── Web scraper (Stockbit broker summary, foreign flow)
   ├── Web scraper (IDX official — company profiles)
   ├── Google News RSS (catalysts)
   └── Computed (technical indicators — pure pandas/numpy)
```

There is no `pandas-ta` / `numba` dependency. Every indicator is computed with
plain pandas, which avoids a multi-minute JIT warm-up on first call.

## Tools

### Market data
| Tool | Description |
|------|-------------|
| `get_stock_price` | Current/last price and trading data |
| `get_financials` | Income statement, balance sheet, valuation ratios |
| `get_technicals` | RSI, MACD, stochastic, moving averages, support/resistance |
| `get_broker_summary` | Top buying/selling brokers (from Stockbit) |
| `get_foreign_flow` | Net foreign buy/sell flow |
| `get_market_overview` | IHSG level, sectors, macro snapshot |
| `get_company_profile` | Company info, ownership, index membership |

### Unified trade workflow (v1.1)
| Tool | Description |
|------|-------------|
| `gather_intelligence` | Step 1 — trade setup (price, SMA, ATR barriers) + news catalysts in one call |
| `evaluate_and_log_thesis` | Step 2 — fee-adjusted Expected Value, then log the thesis for forward testing |
| `evaluate_predictions` | Step 3 — score every logged thesis against the price history that followed |

### Strategy scanners
| Tool | Strategy |
|------|----------|
| `scan_ma_breakout` / `get_top10` / `get_scan_summary` | MA Ketat — converging moving averages |
| `analyze_ticker` / `get_prediction` / `run_backtest` | Single-stock MA Ketat analysis |
| `scan_golden_cross` / `get_top_golden_cross` / `analyze_golden_cross` | Golden cross + stochastic dip-buy |
| `scan_mean_reversion` | Deep oversold — RSI low, price well below SMA20 |
| `scan_volatility_squeeze` | Bollinger width at 6-month low + MACD turning up |
| `scan_volume_accumulation` | Volume spike on a tight intraday range |
| `scan_relative_strength` | Outperformance vs the IHSG, with the RS line at a 3-month high |
| `scan_trend_pullback` | Dip to SMA20 inside a confirmed uptrend, structure intact |
| `scan_breakout_high` | Close above a tight multi-month base (Darvas) on volume |
| `scan_distribution_warning` | RISK scan — charts breaking down, ranked by severity |
| `scan_gap` | Gap-ups that held, or gap-down exhaustion reversals |

All market-wide scans cover the same 178-ticker BEI universe
(`src/data/bei_tickers.json`) and cache their results for 4 hours.

## One universe, ten scans

Every scanner used to run its own batch download over the full universe. Five
scanners meant five downloads of the same bars; ten would have meant ten, and
an agent running the whole ensemble paid the network cost once per strategy.
Scan *results* were cached, but the download underneath them was not.

`src/tools/universe.py` fetches the universe once at `2y` and hands every
scanner a sliced copy. Measured on the 178-ticker universe:

| | Download batches | Wall clock |
|---|---|---|
| All 10 scans, cold cache | 3 (one pass) | ~18 s |
| First scan (pays the fetch) | 3 | ~14 s |
| Each subsequent scan | 0 | 0.1–0.9 s |

Slices are copies, so a column one scanner adds cannot leak into another. The
loader is lock-guarded: several scans entering a cold cache concurrently still
produce a single fetch. An empty fetch is never cached, so an upstream outage
lasts as long as the outage rather than four hours.

## Complete results, not just the top 10

Every scan returns three views of the same ranked list:

| Key | Contents |
|---|---|
| `top_10` | Full detail, best N only |
| `all_signals` | Full detail, **every** signal — length always equals `signals_found` |
| `signals` | Deprecated alias of `top_10`; kept for the existing skill templates |

This matters for the risk scan. `scan_distribution_warning` routinely flags 45+
tickers, and the question you actually ask it is "is the name I am about to buy
on this list?" — which `top_10` cannot answer for anything ranked 11th or worse.
Use `all_signals` for membership tests and vetoes.

## Reading an empty scan

Zero signals is ambiguous — a quiet market and a broken filter look identical
from outside, which is how `scan_volume_accumulation` once stayed silently dead.
All ten scans return a `filter_funnel` of per-stage survivor counts (under
`meta` for the two legacy scanners, top level for the rest). Counts are
monotonically non-increasing and the last stage always equals `signals_found`.

```json
"filter_funnel": {
  "enough_history": 169,
  "passed_volume_floor": 134,
  "closed_above_prior_high": 6,
  "base_tight_enough": 2,
  "volume_confirmed": 0
}
```

Six stocks at 60-day highs is a market with no breakouts, not a scan with bad
defaults. Read the funnel before loosening a filter: the stage that collapses
tells you whether the market is quiet or your threshold is wrong. On a live run
`scan_mean_reversion` drops 134 -> 1 at `rsi_below_threshold`, so its emptiness
is the RSI floor meeting a market with nothing capitulating.

## Partial quotes

Yahoo serves prices from the chart endpoint and metadata from a separate quote
endpoint that is authenticated and rate-limited on its own. When the latter
refuses, `get_stock_price` still returns a real price while the 52-week range,
market cap and previous close come back null. It now says so rather than
leaving nulls beside a healthy-looking price:

```json
{ "price": 6475.0, "partial": true,
  "missing_fields": ["week_52_high", "week_52_low", "market_cap_trillion_idr"],
  "partial_reason": "Yahoo's quote endpoint was unavailable ..." }
```

When the quote is complete, `partial` is `false` and `missing_fields` is absent.

## Installation

### Prerequisites
- Python 3.12+

### Global install (recommended)

Installs an isolated environment and puts an `idx-mcp` executable on your PATH:

```bash
uv tool install --editable /path/to/idx-mcp
```

Then register it with Claude Code for every project:

```bash
claude mcp add --scope user idx-mcp -- "$(which idx-mcp)"
claude mcp get idx-mcp     # should report: Status: ✔ Connected
```

`--editable` means edits to the source are picked up on the next server start,
with no reinstall.

### Development install

```bash
cd idx-mcp
uv venv && uv pip install -e ".[dev]"
```

### Claude Desktop configuration

```json
{
  "mcpServers": {
    "idx-mcp": {
      "command": "/home/<you>/.local/bin/idx-mcp"
    }
  }
}
```

Restart Claude Desktop afterwards. It should report 27 tools.

## Data and log locations

Everything the server writes lives under `~/.idx-mcp/`, so the package directory
stays read-only and can be installed anywhere:

| Path | Contents |
|------|----------|
| `~/.idx-mcp/logs/error.log` | Server log |
| `~/.idx-mcp/logs/predictions_log.json` | Forward-testing thesis log |

Set `IDX_MCP_HOME` to relocate both.

## Base rates for every strategy

`run_backtest` covered MA Ketat alone, so nine of the ten scans shipped with no
evidence: they could produce a signal every day and nothing in the system could
say whether that signal had ever been worth acting on. It now replays any or all
of them.

The constraint that shapes the design: a backtest must run **the same code the
live scan runs**. Reimplementing each filter as a vectorised expression would be
far faster, but it measures a strategy that is not the one in production, and
the two drift apart silently the moment either is edited. So each strategy is
replayed through its own `_build_signal` — the exact function the scanner calls
— one session at a time.

That is affordable because every indicator is causal (rolling and shift only),
so indicators are computed once over the full history and the frame is *sliced*
rather than recomputed. Slicing an acausal indicator would leak the future into
every bar, so the property is asserted by a test that is itself checked against
a deliberately leaky indicator. An earlier version of that test compared built
signals and passed while proving nothing — a strategy that rarely fires returns
`None` on both sides, and `None == None` is not evidence.

Measurement is deliberately plain and identical across strategies: enter at the
signal bar's close, exit at the close `horizon_days` later. No stops, no
targets. That is what makes strategies comparable to each other; trade-level
outcomes with real levels are what `evaluate_predictions` is for.

Two details that decide whether the numbers mean anything:

* **Direction.** A risk scan flags weakness, so a *fall* after the signal is the
  strategy being right. `strategy_return_pct` is signed from the strategy's
  point of view. Scoring `scan_distribution_warning` like a long entry would
  report a working warning system as a failing one, and `scan_gap` reads its
  direction off each individual signal.
* **Sample size.** `n_signals` is reported next to every win rate because it is
  the number that decides whether the win rate is worth reading. Under about 20
  signals is an anecdote.

A strategy whose warm-up exceeds the fetched history is reported as
`not_evaluated` with what it needed, rather than as a truthful-looking zero.

## The forward test

`evaluate_and_log_thesis` writes a thesis; `evaluate_predictions` scores it. Until
the latter existed the log was write-only — theses accumulated and nothing ever
read them back, so every `win_prob` in the system was an assertion no evidence
could contradict.

Scoring walks the daily bars from the session **after** a thesis was logged and
reports which came first, the target or the stop:

| Outcome | Meaning |
|---|---|
| `hit_target` / `hit_stop` | Resolved. These are the only outcomes counted in the win rate |
| `expired` | Target date passed with neither level touched; marked out at the last close |
| `open` | Still live and inside its target date |
| `pending` | Logged after the most recent *settled* close; no completed session to judge it on yet |
| `no_data` / `no_levels` | Unscorable — a failed fetch, or a pre-v3 record with no recoverable levels |

Two deliberate choices keep it honest:

* **Entry timing.** Scoring starts the session after the log timestamp. A thesis
  written against today's close could not have been entered today, and grading
  it from that bar would let it use information it did not have.
* **Same-bar ambiguity.** When one session's high clears the target *and* its low
  takes out the stop, daily bars cannot order the two touches. That resolves to
  a stop and is flagged. A forward test that settles its own ambiguities in its
  favour is not a test.
* **Settled sessions only.** The session currently trading is excluded until
  16:15 WIB. Its high and low are running extremes, not final ones, so a target
  touched at 09:38 can still be followed by a stop before the close — and under
  the rule above that flips the outcome from a win to a loss. Scoring against a
  live bar therefore resolves theses early *and* in the optimistic direction.
  Dropping the NaN-close bar is not sufficient here: once trading opens, Yahoo
  publishes a real OHLC row that is revised tick by tick, and it passes that
  filter untouched. Scanners keep the live bar deliberately; anything that
  scores an outcome does not.

### Levels sit on the IDX tick grid

Every price on IDX sits on a piecewise grid whose step widens with price — 1
below 200, then 2, 5, 10, and 25 above 5,000. A level off that grid cannot be an
order, so it cannot be a fill: scoring an outcome at one reports a trade that was
never available. Levels are therefore snapped when they are logged and again
when they are read, since the v3 migration persisted reconstructed levels before
this rule existed.

Snapping is pessimistic, matching how same-bar ambiguity is resolved: longs round
up and shorts round down, so entry costs more, the stop sits nearer, and the
target sits further. Rounding to nearest would let half of all reconstructed
levels drift in the flattering direction. A trade whose legs are narrower than
one tick collapses onto a single price and is rejected rather than snapped.

`calibration_gap` is `mean_predicted_win_prob - realized_win_rate`. Positive means
the logged theses were optimistic about themselves. It is reported per strategy,
which is the point: it says which scans deserve to be trusted.

## A payload never asserts what its inputs cannot support

Three separate bugs shared one shape: a default value flowing into a field that
reads as a measurement.

* `get_broker_summary` computed `institutional_net = sum(rows)`. With no rows
  that is `0`, and `0 > 0` is false, so a failed scrape fell through to
  `"distribution"` / `"selling"` — a confidently bearish read of nothing at all.
  A verdict now requires rows, a genuinely balanced book reads `"balanced"`
  rather than distribution, and `data_available` says which case you are in.
* `get_foreign_flow` initialised its counters to `0` and formatted them as
  `"IDR 0 (neutral)"`. Unmeasured is not neutral; those fields are now null with
  an `unavailable_reason`.
* `get_company_profile` read `Ticker.major_holders` positionally, taking
  `row.iloc[1]` as a shareholder name — a column that does not exist — so every
  entry was `{"name": "N/A"}`. `institutionsCount` was reported as a percentage,
  putting "90.0" beside real 0.85% figures, and the four junk rows evicted the
  genuine institutional holders from the top-5 slice. Labels are now read into
  named fields under `ownership_breakdown`, and an unrecognised shape yields
  missing fields rather than wrong ones.

Two related fixes remove ambiguity rather than fabrication:

* `dividend_yield_pct` multiplied by 100 unconditionally. yfinance changed that
  field from a fraction to a percentage, so ISAT reported 439% and DMAS 829%.
  The value alone cannot be disambiguated inside `[0, 1]`, so the yield is now
  derived from the dividend rate and price where possible; the scale heuristic
  is a labelled fallback, and an implausible result is dropped.
* `roic_pct` on the quarterly report is a single period's return and is not
  comparable to the annual figure. The column may be a quarter or a half, so
  multiplying blindly would invent the difference — it carries a `roic_basis`
  instead.
* `days_since_cross` is null both when a stock never crossed and when it crossed
  before the lookback began — opposite situations. `cross_age` distinguishes
  them, and `detectable_cross_sessions` reports how narrow the window really is
  (43 sessions on a 1y fetch, since SMA200 needs 200 bars first).

## Trade levels are required

`evaluate_and_log_thesis` requires `entry_price`, `stop_loss` and `target_price`.
Schema 2 recorded only IDR magnitudes, which meant nothing could later look at
the price history and say whether a thesis worked — the log physically could not
be scored. Levels are validated against `direction`, so a transposed stop and
target is rejected rather than silently inverting every future outcome.

Older records are upgraded by `migrate_predictions_log()`, which recovers levels
from prose where they were written into `reasoning`, otherwise reconstructs them
from the logged IDR ratios against the close on the log date. Each entry records
its `levels_source` — `declared`, `parsed_from_reasoning`,
`reconstructed_from_ratios` or `unresolvable` — so weaker evidence stays visibly
weaker.

## Concurrent writers

The predictions log has more than one writer: a Claude Code session and the
Antigravity app each run their own server process. Both do read, append, write.
An advisory `flock` spans the whole cycle, because a threading lock only
serialises writers inside one interpreter. With the file lock disabled, four
processes appending 12 entries each lose 2 of 48 — measured, not theorised.

## Expected Value semantics

`evaluate_and_log_thesis` requires `position_value_idr` — the transaction value
of the intended position. IDX brokerage fees are charged on transaction value,
not on the profit or loss of the trade:

```
fees_total = position_value_idr * (buy_fee_rate + sell_fee_rate)
net_win    = profit_target_idr - fees_total
net_loss   = loss_target_idr   + fees_total
ev         = win_prob * net_win - (1 - win_prob) * net_loss
```

`profit_target_idr` and `loss_target_idr` are positive IDR magnitudes. The
response also returns `breakeven_win_prob` and `edge_vs_breakeven`.

## Testing

```bash
uv run pytest -m "not network"   # fast, no network — ~2 s
uv run pytest                    # includes live tests against real IDX data
```

The `network`-marked tests make real calls to Yahoo Finance and Google News and
assert on invariants that hold on any trading day, not on specific prices.

## Caching

In-memory with TTL:
- Price/flow data: 5 minutes
- Fundamentals/profile: 1 hour
- Market-wide scans: 4 hours

## Error handling

Tools never raise across the protocol boundary — failures come back as
structured JSON with `error`, `error_type`, `message`, and `suggestion`.
Missing arguments return `error_type: "invalid_arguments"`.

Import failures are the exception: if dependencies are missing the server exits
with a diagnostic on stderr rather than starting up and advertising tools that
cannot run.

## Project structure

```
idx-mcp/
├── pyproject.toml
├── requirements.txt
├── uv.lock
├── README.md
├── run_server.py              # standalone entry point
├── src/
│   ├── server.py              # MCP server: tool registry + dispatch
│   ├── mcp_compat.py          # adapts to mcp SDK 1.x and 2.x
│   ├── tools/                 # 26 tool implementations
│   │   ├── universe.py        # shared universe fetch backing all 10 scans
│   ├── scrapers/              # Web scraping modules
│   ├── utils/                 # Cache, formatting, tickers, paths, OHLCV hygiene
│   └── data/                  # Bundled reference data (JSON)
├── skills/                    # stock-analyst-pro Claude skill
└── tests/                     # unit + live integration suites
```
