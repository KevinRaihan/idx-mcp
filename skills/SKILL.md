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
6. `get_stock_news` → recent catalysts
7. `get_company_profile` → ownership, index membership, BUMN flag
8. `get_market_overview` → IHSG, macro context

MA Ketat tools (add when user requests screening or breakout analysis):
- `scan_today` / `get_top10` / `get_scan_summary` → market-wide breakout screening
- `analyze_ticker` → single-stock MA Ketat signal check (add to full analysis when relevant)
- `get_prediction` → rule-based short-term forecast (use after confirmed MA Ketat signal)
- `run_backtest` → historical signal validation for a specific ticker

Golden Cross tools (add when user requests golden cross / dip-buy setups):
- `scan_golden_cross` / `get_top_golden_cross` → market-wide golden cross dip-buy screening
- `analyze_golden_cross` → single-stock Golden Cross + Stochastic analysis (add to full analysis when relevant)

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
