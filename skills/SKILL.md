---
name: stock-analyst-pro
description: >
  Quantitative stock analysis skill for IDX (Indonesia Stock Exchange). Use when the user asks
  to analyze, screen, or evaluate any IDX-listed stock ticker (e.g. "analyze BBCA", "should I
  buy TLKM", "quick analysis GOTO", "screen ASII"). Also use for sector/market overviews,
  broker flow analysis, or any question involving IDX stock data, prices, fundamentals, or
  technicals. Triggers on: ticker symbols (2–4 uppercase letters), "IDX", "IHSG", "saham",
  "buy/sell/hold recommendation", "bandarmology", "foreign flow", "broker summary",
  "golden cross", "dip buy", "stochastic oversold", "uptrend dip".
---

# StockAnalyst Pro Skill

Quantitative-first IDX stock analysis. Every output is numbers-driven — tables over prose,
scores over opinions, factors over stories.

## Mandatory Workflow (run every time, in order)

### Step 0 — Market Status
Compute current WIB time. State:
- `Session 1 OPEN` (09:00–12:00 WIB)
- `BREAK` (12:00–13:30 WIB)
- `Session 2 OPEN` (13:30–16:15 WIB)
- `CLOSED` (outside hours or weekend)

### Step 1 — Data Retrieval via IDX-MCP
**Always use IDX-MCP tools first.** Read [references/idx-mcp-guide.md](references/idx-mcp-guide.md)
for exact tool names, parameters, response field mappings, and chaining sequences.

Mandatory tool call sequence for a full analysis:
1. `get_stock_price` → price, volume, change%, market cap
2. `get_financials` (annual) + `get_financials` (quarterly) → all ratios and statements
3. `get_technicals` (6mo period) → MAs, RSI, MACD, support/resistance
4. `get_broker_summary` → institutional flow, broker codes
5. `get_foreign_flow` → net foreign daily/weekly/monthly
6. `gather_intelligence` → trade setup (price, SMA, ATR barriers) + recent catalysts
7. `get_company_profile` → ownership, index membership, BUMN flag
8. `get_market_overview` → IHSG, macro context

MA Ketat tools (add when user requests screening or breakout analysis):
- `scan_ma_breakout` / `get_top10` / `get_scan_summary` → market-wide breakout screening
- `analyze_ticker` → single-stock MA Ketat signal check (add to full analysis when relevant)
- `get_prediction` → rule-based short-term forecast (use after confirmed MA Ketat signal)
- `run_backtest` → historical signal validation for a specific ticker

Golden Cross tools (add when user requests golden cross / dip-buy setups):
- `scan_golden_cross` / `get_top_golden_cross` → market-wide golden cross dip-buy screening
- `analyze_golden_cross` → single-stock Golden Cross + Stochastic analysis (add to full analysis when relevant)

Ensemble scanners (v1.1 — add when the user asks "what should I buy today" or names a strategy):
- `scan_mean_reversion` → deep oversold capitulation (RSI low, price well below SMA20).
  Params: `rsi_threshold` (30), `min_volume` (500k), `min_below_sma20_pct` (5.0)
- `scan_volatility_squeeze` → Bollinger width at a 6-month low with MACD turning up.
  Params: `min_volume` (1M), `squeeze_tolerance` (1.10)
- `scan_volume_accumulation` → volume spike on a tight intraday range and an up close.
  Params: `min_volume` (1M), `vol_multiple` (3.0), `max_spread_pct` (5.0)

Ensemble scanners (v1.2 — five more strategies over the same universe):
- `scan_relative_strength` → outperformance vs the IHSG over 1/3/6 months, RS line vs its
  3-month high. Use it to sanity-check any other signal: a strong chart lagging the index
  is a weak hand. Params: `min_volume` (500k), `min_excess_3m_pct` (5.0), `require_rs_high` (false)
- `scan_trend_pullback` → dip inside a confirmed uptrend (above SMA200, SMA50>SMA200, below
  SMA20, RSI 40-58, 20-day low above the 60-day low). Distinct from `scan_mean_reversion`,
  which has no trend requirement and will return falling knives.
  Params: `min_volume` (500k), `rsi_min` (40.0), `rsi_max` (58.0), `max_pullback_pct` (15.0)
- `scan_breakout_high` → close above a tight multi-month base on volume (Darvas box).
  Returns a `filter_funnel` explaining an empty result.
  Params: `min_volume` (500k), `lookback_days` (60), `vol_multiple` (1.5), `max_base_range_pct` (25.0)
- `scan_distribution_warning` → **RISK scan, not a buy list.** Charts breaking down, ranked by
  severity; a higher score is a worse chart. Use it to exit/trim held positions, or to veto a
  long signal another scan produced on the same ticker.
  Params: `min_volume` (500k), `min_warning_score` (50.0)
- `scan_gap` → gap-ups that held, or gap-down exhaustion reversals. Reports each gap as a share
  of the IDX auto-rejection (ARA/ARB) band. Reads the last *completed* daily bar, so while the
  market is open it reflects the previous session.
  Params: `min_volume` (500k), `min_gap_pct` (2.0), `direction` ("up"/"down"/"both")

Every scan returns `universe_size`, `tickers_with_data`, `signals_found` and a `top_10`
array sorted by `confidence_score`. **Zero signals is a valid answer** — report it as a
market condition rather than retrying. If the user wants more candidates, loosen the
filters explicitly (e.g. `max_spread_pct: 8.0`) and say which ones you relaxed.

### Step 1b — Trade thesis (only when the user wants a position sized)
1. `gather_intelligence(ticker)` → setup + catalysts
2. Determine `win_prob` from the evidence
3. `evaluate_and_log_thesis(...)` → fee-adjusted EV, logged for forward testing

`evaluate_and_log_thesis` requires `position_value_idr` — the IDR transaction value of
the intended position (entry price × shares). IDX fees are charged on transaction value,
not on the profit/loss targets, so a thesis without it cannot be priced. Pass
`profit_target_idr` and `loss_target_idr` as **positive magnitudes**. Report the returned
`ev_idr`, `breakeven_win_prob` and `edge_vs_breakeven`; a negative EV is still logged and
should be reported as a no-trade.

Supplement with web search ONLY when MCP data returns null/unavailable for a critical field.
Tag every data point with confidence: `[H]` = High, `[M]` = Medium, `[L]` = Low.
See [references/idx-mcp-guide.md](references/idx-mcp-guide.md) for confidence assignment rules.

### Step 2 — Factor Scorecard
Compute the 6-factor composite score (1.0–5.0 scale).
Read [references/factor-scoring.md](references/factor-scoring.md) for complete scoring tables,
weights, thresholds, sector adjustments, and the Liquidity Gate rule.

Output: Completed composite table → Rating label.

### Step 3 — Technical Snapshot
Single table output. All values from `get_technicals` MCP response.
Formulas for derived fields in [references/analytics-calculations.md](references/analytics-calculations.md).

### Step 4 — Fundamental Snapshot
Three tables: Valuation | Growth | Quality & Health.
All ratio formulas and sector-specific overrides in [references/analytics-calculations.md](references/analytics-calculations.md).

### Step 5 — Risk & Catalyst Matrix
Two tables: Risk Matrix | Upcoming Catalysts.
Auto-trigger special flags (ARA/ARB, illiquid, BUMN, conglomerate) per rules in
[references/factor-scoring.md](references/factor-scoring.md).

### Step 6 — Trade Parameters & Scenarios
Trade Setup table + Bull/Base/Bear scenario table with probability-weighted return.
Calculation method for entry/stop/TP levels in [references/analytics-calculations.md](references/analytics-calculations.md).

## Output Scaling

| User Intent | Steps to Run | Format |
|---|---|---|
| Full analysis / "Should I buy?" | All 6 steps | Full report |
| "Brief" / "quick" | Steps 2, 5, 6 | Scorecard + Risk + Trade only |
| Price check | Step 1 only | Header line + 1 context sentence |
| Sector overview | Steps 1 (market overview) + 2 for top picks | Comparative scorecard table |
| Chart image uploaded | Step 3 first, then available Steps 2, 4 | Technical-first |
| Market scan / "What to buy today" | get_scan_summary → get_top10 → analyze_ticker top 3 | Template 12 + 13 + 14 |
| MA Ketat deep dive (single stock) | analyze_ticker + get_prediction + run_backtest | Templates 13 + 14 + 15 |
| Validate signal reliability | run_backtest | Template 15 only |
| Golden Cross scan / "buy the dip" | get_top_golden_cross → analyze_golden_cross top 3 | Template 16 + 17 |
| Named strategy scan (oversold / squeeze / accumulation) | the matching scan_* tool → analyze_ticker top 3 | Scan table + Template 13 |
| "Size this trade" / "what is the EV?" | gather_intelligence → evaluate_and_log_thesis | EV table |
| Golden Cross deep dive (single stock) | analyze_golden_cross | Template 17 only |

## Output Format Rules
- **Zero prose sections.** All output is tables, scores, or flags.
- Use exact templates from [references/output-templates.md](references/output-templates.md)
- Header block required on every report:
  ```
  📊 [TICKER] — [Company] | [Sector] | IDR X,XXX (+X.X%) | [DD-MMM-YYYY HH:MM WIB]
  Mkt Cap: IDR X.XTr | Float: X% | COMPOSITE: X.X/5.0 → [RATING] | Confidence: H/M/L
  ```
- Sources footer required on every report — cite MCP timestamp + any web sources used
- Disclaimer line required at end

## References
- [references/idx-mcp-guide.md](references/idx-mcp-guide.md) — MCP tool reference, response field mappings, chaining, error handling
- [references/factor-scoring.md](references/factor-scoring.md) — 6-factor engine, weights, thresholds, sector adjustments, special flags
- [references/analytics-calculations.md](references/analytics-calculations.md) — All formulas: ratios, TP/SL, scenarios, derived metrics
- [references/output-templates.md](references/output-templates.md) — Copy-paste table templates for every report section
