# idx-mcp — IDX Stock Data MCP Server

A local MCP (Model Context Protocol) server that provides Indonesian Stock Exchange (IDX) data to Claude Code and Claude Desktop. Exposes **26 tools** returning structured JSON for stock prices, financials, technicals, broker flow, market-wide strategy scans, and trade-thesis evaluation.

**Version 1.2.0** — unified 2-step workflow plus a ten-strategy scanner ensemble over one shared universe fetch.

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

## Reading an empty scan

Zero signals is ambiguous — a quiet market and a broken filter look identical
from outside, which is how `scan_volume_accumulation` once stayed silently dead.
`scan_breakout_high` returns a `filter_funnel` of per-stage survivor counts:

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
defaults.

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

Restart Claude Desktop afterwards. It should report 26 tools.

## Data and log locations

Everything the server writes lives under `~/.idx-mcp/`, so the package directory
stays read-only and can be installed anywhere:

| Path | Contents |
|------|----------|
| `~/.idx-mcp/logs/error.log` | Server log |
| `~/.idx-mcp/logs/predictions_log.json` | Forward-testing thesis log |

Set `IDX_MCP_HOME` to relocate both.

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
