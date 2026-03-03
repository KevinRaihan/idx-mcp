# IDX-MCP Tool Reference Guide

Complete reference for the `idx-mcp` server. Use this to know exactly what to call,
what parameters to pass, what fields are returned, and how to chain calls together.

---

## Tool Inventory

| Tool | Primary Use |
|---|---|
| `get_stock_price` | Current/last price, volume, basic market data |
| `get_financials` | Income statement, balance sheet, ratios (annual or quarterly) |
| `get_technicals` | Moving averages, RSI, MACD, Stochastic, support/resistance |
| `get_broker_summary` | Top buying/selling brokers, net flow by broker code |
| `get_foreign_flow` | Net foreign buy/sell (daily, weekly, monthly) |
| `get_stock_news` | Recent news from Kontan, Bisnis, CNBC Indonesia |
| `get_company_profile` | Ownership, index membership, BUMN flag, conglomerate group |
| `get_market_overview` | IHSG, sectoral indices, macro (USD/IDR, BI rate, commodities) |
| `scan_today` | Full MA Ketat scanner across ~250 stocks — ranked breakout signals |
| `get_top10` | Top 10 MA Ketat signals from cached/fresh scan (lightweight) |
| `analyze_ticker` | Deep MA Ketat analysis for a single stock — pass/fail + prediction |
| `get_prediction` | Rule-based short-term directional forecast (3–10 days) |
| `run_backtest` | Historical MA Ketat backtest — win rate, avg return, max drawdown |
| `get_scan_summary` | Natural language narrative of today's MA Ketat scan results |
| `scan_golden_cross` | Full Golden Cross + Stochastic Oversold scan — ranked dip-buy signals |
| `get_top_golden_cross` | Top 10 Golden Cross dip-buy signals from cached/fresh scan (lightweight) |
| `analyze_golden_cross` | Deep Golden Cross + Stochastic analysis for a single stock — pass/fail + prediction |

---

## Tool 1: `get_stock_price`

**When to call:** Always first. Establishes price anchor for all ratio calculations.

**Parameters:**
```
ticker: string  — IDX ticker code, e.g. "BBCA", "TLKM", "GOTO"
```

**Key response fields to extract:**
```
price              → Current/last traded price (IDR per share)
change             → Absolute price change from previous close
change_percent     → % change from previous close
volume             → Shares traded today
value              → IDR value traded today (volume × price)
market_cap         → Total market capitalization (IDR)
market_status      → "open" or "closed"
52_week_high       → Highest price in past 52 weeks
52_week_low        → Lowest price in past 52 weeks
```

**Derived calculations (do these yourself):**
```
Distance from 52W high  = (price / 52_week_high) - 1   [negative = below high]
Distance from 52W low   = (price / 52_week_low) - 1    [positive = above low]
```

**Confidence assignment:**
- Market OPEN + response has timestamp < 30min old → `[H]`
- Market CLOSED (using last close) → `[M]`
- Response missing timestamp → `[M]`

---

## Tool 2: `get_financials`

**When to call:** Twice — once with `period="annual"`, once with `period="quarterly"`.
Annual for trends and ratios; quarterly for most recent growth rates.

**Parameters:**
```
ticker: string
period: "annual" | "quarterly"   — default is "annual"
```

**Key response fields to extract (annual):**

*Income Statement:*
```
revenue            → Total revenue / net sales
gross_profit       → Revenue minus COGS
operating_income   → EBIT (before interest and tax)
ebitda             → Earnings before interest, tax, depreciation, amortization
net_income         → Bottom line profit
eps                → Earnings per share (basic)
```

*Balance Sheet:*
```
total_assets
total_equity       → Shareholders' equity / book value
total_debt         → Short-term + long-term interest-bearing debt
cash               → Cash and cash equivalents
current_assets
current_liabilities
```

*Pre-computed Ratios (use directly if present):*
```
pe_ratio           → Price-to-earnings (TTM)
pb_ratio           → Price-to-book
ev_ebitda          → Enterprise value / EBITDA
roe                → Return on equity (%)
roa                → Return on assets (%)
debt_to_equity     → D/E ratio
current_ratio      → Current assets / current liabilities
net_margin         → Net income / revenue (%)
gross_margin       → Gross profit / revenue (%)
dividend_yield     → Annual dividend / price (%)
```

**Key response fields to extract (quarterly):**
Same fields as annual, but use to compute:
- Latest quarter revenue vs same quarter prior year → YoY revenue growth
- Latest quarter net income vs same quarter prior year → YoY earnings growth
- Trailing 4 quarters summed → TTM figures if annual data is stale

**If ratio fields are missing from MCP response, compute from raw fields:**
See [analytics-calculations.md](analytics-calculations.md) for all formulas.

**Confidence assignment:**
- Annual data within current fiscal year → `[H]`
- Annual data from prior fiscal year → `[M]`
- Quarterly data within last 2 quarters → `[H]`
- Quarterly data older than 2 quarters → `[M]`

---

## Tool 3: `get_technicals`

**When to call:** Use `period="6mo"` as default. Use `"1y"` if user asks for long-term view
or if stock has had unusual recent movements that need more context.

**Parameters:**
```
ticker: string
period: "3mo" | "6mo" | "1y"   — default "3mo", use "6mo" for standard analysis
```

**Key response fields to extract:**
```
ema_20             → 20-day exponential moving average
sma_50             → 50-day simple moving average
sma_200            → 200-day simple moving average
rsi                → RSI(14) value
macd               → MACD line value
macd_signal        → MACD signal line value
macd_histogram     → MACD - signal (positive = bullish)
stoch_k            → Stochastic %K
stoch_d            → Stochastic %D
volume_avg_20      → 20-day average daily volume
support_1          → Nearest support level
support_2          → Second support level
support_3          → Third support level (if available)
resistance_1       → Nearest resistance level
resistance_2       → Second resistance level
resistance_3       → Third resistance level (if available)
trend              → Overall trend signal from MCP ("bullish"/"bearish"/"neutral")
```

**Derived signals (compute from raw values):**
```
Price vs EMA20      = (price / ema_20) - 1
Price vs SMA50      = (price / sma_50) - 1
Price vs SMA200     = (price / sma_200) - 1
MACD Cross signal   = if macd > macd_signal → "Bullish" else "Bearish"
Volume signal       = if today_volume > volume_avg_20 → "Above avg" else "Below avg"
Golden cross        = sma_50 > sma_200 → "Golden Cross (bullish)"
Death cross         = sma_50 < sma_200 → "Death Cross (bearish)"
```

**ARA/ARB Auto-Calculation (IDX-specific):**
IDX sets auto-rejection limits based on price range. Compute:
```
Price range           ARA %    ARB %
≥ IDR 200             +35%     -35%   (standard stocks)
IDR 50–199            +35%     -35%
< IDR 50              +35%     -35%
Note: New listings and certain stocks may have different limits.
For accelerated ARA/ARB (stocks in special monitoring): +10% / -10%

ARA price = price × 1.35   (or applicable % above)
ARB price = price × 0.65
```

**RSI interpretation for scoring:**
```
< 30    → Oversold (watch for reversal)
30–40   → Approaching oversold
40–50   → Mild bearish bias
50–65   → Neutral to mild bullish (optimal entry zone)
65–70   → Mild overbought
70–80   → Overbought (reduce position)
> 80    → Extreme overbought (avoid new entries)
```

**Confidence assignment:**
- `period="6mo"` with current price data → `[H]`
- `period="3mo"` only → `[M]` (limited history for SMA200)

---

## Tool 4: `get_broker_summary`

**When to call:** For flow analysis and detecting institutional accumulation/distribution.

**Parameters:**
```
ticker: string
```

**Key response fields to extract:**
```
top_buyers[]       → Array of {broker_code, broker_name, net_buy_volume, net_buy_value}
top_sellers[]      → Array of {broker_code, broker_name, net_sell_volume, net_sell_value}
net_foreign_value  → Net foreign buy/sell in IDR (positive = net buy)
```

**Broker Code Classification:**
Use this table to identify broker type from code:

| Code | Broker | Type |
|---|---|---|
| YP | Mirae Asset Sekuritas | Foreign/Retail heavy |
| CC | Mandiri Sekuritas | Domestic institutional |
| DX | BNI Sekuritas | Domestic institutional |
| MS | Morgan Stanley | Foreign institutional |
| RX | Macquarie | Foreign institutional |
| CG | CGS-CIMB | Foreign institutional |
| BK | BCA Sekuritas | Domestic institutional |
| AK | CLSA | Foreign institutional |
| LG | Trimegah Sekuritas | Domestic mid-tier |
| ZP | Kim Eng (Maybank) | Foreign institutional |
| DR | OSO Sekuritas | Domestic retail |
| OD | Mirae (old code) | Foreign/Retail |
| CP | Valbury | Domestic retail |

**Flow interpretation logic:**
```
If top buyers contain 2+ foreign institutional codes (MS, RX, CG, AK, ZP):
  → "Foreign institutional accumulation" → Flow score = 5

If top buyers are domestic institutional (CC, DX, BK):
  → "Domestic institutional accumulation" → Flow score = 4

If mixed buy/sell across foreign + domestic:
  → "Neutral/contested" → Flow score = 3

If top sellers are foreign institutional with large net_sell_value:
  → "Foreign institutional distribution" → Flow score = 1-2

If net_foreign_value is deeply negative (>5% of market cap sold):
  → "Heavy foreign selling" → Flow score = 1
```

**Bandarmology signals:**
```
Accumulation signal = top_buyers has concentrated positions (1-2 brokers dominating >40% of buy volume)
Distribution signal = top_sellers has concentrated positions
Bandar behavior     = same broker appears in both top buyers AND sellers (position building via wash-like trades)
```

**Confidence:** Data scraped from Stockbit — typically `[M]` unless confirmed with foreign flow tool.

---

## Tool 5: `get_foreign_flow`

**When to call:** Always call for any stock analysis. Cross-reference with broker_summary.

**Parameters:**
```
ticker: string       — specific stock (omit for market-wide flow)
period: "daily" | "weekly" | "monthly"   — default "daily"
```

**Recommended: Call 3 times** — daily, weekly, monthly — for complete flow picture.

**Key response fields to extract:**
```
net_buy            → Net foreign buy/sell value in IDR (positive = net buy)
buy_value          → Total foreign buy value
sell_value         → Total foreign sell value
cumulative_flow    → Running cumulative net over the period
```

**Flow trend classification:**
```
For 30-day monthly net_buy:
> +50B IDR           → Strong accumulation  → Score 5
+10B to +50B         → Mild accumulation    → Score 4
-10B to +10B         → Neutral              → Score 3
-50B to -10B         → Mild distribution   → Score 2
< -50B               → Strong distribution → Score 1

Scale thresholds proportionally for small-cap stocks:
Divide thresholds by (stock market cap / 10 trillion) to normalize.
```

**Divergence signals (important):**
```
Price UP + foreign flow SELLING  → Potential distribution, caution
Price DOWN + foreign flow BUYING → Potential accumulation, bullish divergence
Price UP + foreign flow BUYING   → Confirming uptrend
Price DOWN + foreign flow SELLING → Confirming downtrend
```

---

## Tool 6: `get_stock_news`

**Parameters:**
```
ticker: string
limit: integer   — max 20, use 10 for standard analysis
```

**Key response fields to extract:**
```
articles[].title       → Headline
articles[].source      → Publication (Kontan, Bisnis, CNBC Indonesia)
articles[].date        → Publication date
articles[].summary     → Article summary (if available)
```

**Catalyst categorization:**
```
Earnings release    → Check beat/miss vs estimates
Dividend announcement → Record date, ex-date, yield
Rights issue / stock split → Dilution risk or technical adjustment
Corporate action    → Merger, acquisition, divestment
Regulatory news     → New permits, revocations, policy changes
Commodity prices    → Impact if sector-relevant
Management change   → CEO/CFO turnover = governance risk flag
```

**News sentiment scoring for Factor 6:**
```
3+ positive catalysts in last 30 days → Sentiment +1 to flow score
3+ negative catalysts → Sentiment -1 to flow score
No material news → Neutral
```

---

## Tool 7: `get_company_profile`

**Parameters:**
```
ticker: string
```

**Key response fields to extract:**
```
company_name           → Full legal company name
sector                 → JASICA sector classification
subsector              → More specific sub-sector
major_shareholders[]   → Array of {name, percentage}
free_float_percent     → % of shares available for public trading
is_bumn               → Boolean — state-owned enterprise flag
conglomerate_group     → Parent group name (Salim, Sinar Mas, Astra, etc.)
index_membership[]     → ["LQ45", "IDX30", "ISSI", "IDX80", etc.]
listing_date           → IPO date on IDX
```

**BUMN special handling:**
```
If is_bumn = true → Auto-add to report:
  "⚑ BUMN — Government stake: X%. Subject to mandatory dividend policy,
   political appointment risk, and potential strategic direction changes
   post-election cycle."
```

**Conglomerate mapping:**
```
If conglomerate_group is present:
  Flag: "Part of [Group] conglomerate — assess group-level debt and
  cross-subsidization risk. Check for related-party transactions."

Key groups and their risk profiles:
  Salim Group     → Diversified, strong franchise, generally low risk
  Sinar Mas       → Pulp/palm/finance, moderate leverage risk
  Lippo Group     → Property/hospital focus, watch D/E
  Bakrie Group    → Commodity exposure, historically high leverage risk
  Djarum/BCA      → Conservative, high quality
  Astra/Jardine   → Diversified, strong governance
  Saratoga        → Investment holding, look for NAV discount
  CT Corp         → Media/retail, watch consumer cycle
  MNC Group       → Media/finance, governance scrutiny warranted
```

**Index membership implications:**
```
LQ45 / IDX30 → Highly liquid, eligible for margin trading, index fund inclusion
ISSI         → Syariah-compliant — no conventional debt above threshold
MSCI EM      → Foreign institutional mandate eligible
IDX80        → Broader liquid universe
```

**Free float and liquidity cross-check:**
```
If free_float_percent < 15% → Liquidity Gate risk — see factor-scoring.md
If free_float_percent < 5%  → ⛔ CRITICAL: Near-zero liquidity, recommend avoid
```

---

## Tool 8: `get_market_overview`

**Parameters:**
```
include_macro: boolean   — default true, always use true
```

**Key response fields to extract:**
```
ihsg_price             → IHSG composite index level
ihsg_change_percent    → IHSG daily change %
sectoral_indices[]     → Array of {sector, index_value, change_percent}
usd_idr                → USD/IDR exchange rate
bi_rate                → Bank Indonesia benchmark rate (%)
inflation_rate         → Latest CPI inflation reading (%)
coal_price             → Newcastle coal price (USD/ton)
cpo_price              → Crude Palm Oil price (MYR/ton or USD)
nickel_price           → LME Nickel price (USD/ton)
gold_price             → Gold price (USD/oz)
```

**Market context signals:**
```
IHSG change > +1%    → Risk-on, positive backdrop
IHSG change -1% to +1% → Neutral
IHSG change < -1%    → Risk-off, apply market risk discount to targets

USD/IDR > 16,500     → Rupiah stress → penalize foreign-debt-heavy companies
USD/IDR 15,500–16,500 → Neutral
USD/IDR < 15,500     → Rupiah strength → positive for importers

BI Rate rising       → Negative for property, leveraged companies
BI Rate stable       → Neutral
BI Rate falling      → Positive for rate-sensitive sectors (banking NIM, property)
```

---

---

## Tool 9: `scan_today`

**When to call:** User asks "what to buy today", "screen the market", "find breakout setups",
or any broad market scanning request. **Warning:** takes 30–90 seconds due to bulk data download.

**Parameters:**
```
tick_threshold: number  — Max tick-adjusted MA spread (default 6.0; lower = stricter)
vol_threshold:  number  — Max 10-day rolling volatility % (default 3.8; lower = stricter)
```

**Key response fields to extract:**
```
signals[]           → Array of stocks that pass MA Ketat filters
  .ticker           → IDX ticker symbol
  .confidence_score → Signal strength score (higher = stronger)
  .ma_spread_ticks  → How tight the MAs are (lower = tighter compression)
  .volatility       → 10-day rolling volatility %
  .last_price       → Last traded price
  .signal           → Signal description string
total_scanned       → Number of stocks processed
signals_found       → Count of passing signals
scan_timestamp      → When the scan was run
```

**Usage pattern:**
```
1. Run scan_today with default params first
2. Take top 3–5 by confidence_score
3. Run analyze_ticker on each for detailed confirmation
4. Run get_prediction for entry parameters on confirmed signals
```

**Confidence:** `[M]` — scanner uses daily close data, not real-time. Scan validity ~1 trading day.

---

## Tool 10: `get_top10`

**When to call:** Faster alternative to `scan_today` when you just want the top ranked signals.
Uses cached results if today's scan has already run; triggers a fresh scan if not.

**Parameters:** None

**Key response fields to extract:**
```
top_signals[]       → Array of top 10 ranked stocks (same schema as scan_today signals[])
  .ticker
  .confidence_score
  .ma_spread_ticks
  .volatility
  .last_price
  .signal
cache_used          → true = using cached scan, false = fresh scan ran
scan_date           → Date of the underlying scan
```

**Confidence:** `[M]` — if cache_used=true, verify scan_date matches today before acting.

---

## Tool 11: `analyze_ticker`

**When to call:** After identifying a candidate from scan_today/get_top10, OR when user asks
for MA Ketat analysis on a specific stock. Also use as an additional layer in full single-stock analysis.

**Parameters:**
```
ticker: string
period: "3mo" | "6mo" | "1y"   — default "6mo"
```

**Key response fields to extract:**
```
ma_values{}         → All moving average values
  .sma_3            → 3-day SMA
  .sma_5            → 5-day SMA
  .sma_10           → 10-day SMA
  .sma_20           → 20-day SMA
  .sma_50           → 50-day SMA
ma_spread_ticks     → Tick-adjusted range across all 5 MAs (lower = tighter)
volatility_10d      → 10-day rolling volatility %
passes_entry        → Boolean — does stock currently pass all MA Ketat entry filters?
fail_reason         → If passes_entry=false, explains why (e.g., "spread too wide")
signal_present      → Boolean — active MA Ketat signal right now?
prediction{}        → If signal_present=true, contains short prediction (same as get_prediction)
  .expected_gain_low
  .expected_gain_high
  .target_price_1
  .stop_loss
  .horizon_days
```

**MA Ketat interpretation:**
```
ma_spread_ticks ≤ 3      → Very tight compression — strongest signal
ma_spread_ticks 3–6      → Tight compression — valid signal
ma_spread_ticks 6–10     → Moderate — borderline, use higher vol_threshold caution
ma_spread_ticks > 10     → Too loose — not a MA Ketat setup

passes_entry = true  + signal_present = true  → Active setup, proceed to get_prediction
passes_entry = false + signal_present = false → No setup, monitor only
passes_entry = true  + signal_present = false → Near setup but not triggered yet
```

**Confidence:** `[H]` if period="6mo", `[M]` if period="3mo".

---

## Tool 12: `get_prediction`

**When to call:** After confirming a valid signal via analyze_ticker, OR when user asks for
short-term price targets ("where will XXXX go this week", "predict XXXX for 7 days").
**Note:** Rule-based only (no ML). Treat as structured estimation, not a price guarantee.

**Parameters:**
```
ticker:        string
horizon_days:  integer   — Forecast horizon in trading days (3–10, default 7)
```

**Key response fields to extract:**
```
expected_gain_low      → Lower bound of expected % gain
expected_gain_high     → Upper bound of expected % gain
target_price_1         → Primary price target (IDR)
target_price_2         → Secondary/extended target (IDR, if present)
stop_loss              → Suggested stop-loss price (IDR)
reward_risk_ratio      → R:R ratio (target gain / stop distance)
horizon_days           → Forecast period confirmed
basis                  → Description of what drives the forecast
confidence             → Prediction confidence label (e.g., "moderate", "high")
```

**Derived calculations:**
```
Expected gain (midpoint) = (expected_gain_low + expected_gain_high) / 2
Stop distance %          = (entry_price - stop_loss) / entry_price × 100
```

**When R:R < 1.5 → downgrade recommendation; R:R ≥ 2.0 → favorable entry.**

**Confidence:** `[M]` — rule-based, no ML, no real-time flow data. Cross-check with get_technicals.

---

## Tool 13: `run_backtest`

**When to call:** When user asks "does the MA Ketat signal actually work for this stock?",
"what's the historical win rate?", or before committing significant capital to a signal.

**Parameters:**
```
ticker:          string
period:          "1y" | "2y"          — default "1y"
tick_threshold:  number               — MA range_ticks threshold (default 6.0)
vol_threshold:   number               — Volatility threshold % (default 3.8)
```

**Key response fields to extract:**
```
total_signals          → Number of MA Ketat triggers found in the period
win_rate               → % of signals where 7-day forward return was positive
avg_return_7d          → Average 7-day forward return across all signals
max_drawdown           → Worst peak-to-trough drawdown during signal windows
best_trade             → Best individual signal return
worst_trade            → Worst individual signal return
signal_dates[]         → Array of {date, entry_price, return_7d} for each historical signal
```

**Backtest quality assessment:**
```
total_signals < 5      → Insufficient sample — treat with caution [L]
total_signals 5–15     → Small sample — use [M] confidence
total_signals > 15     → Adequate sample — use [H] confidence

win_rate > 65%         → Signal historically reliable for this stock
win_rate 50–65%        → Moderate reliability — use tight stops
win_rate < 50%         → Signal unreliable on this ticker — avoid or reduce size
```

**Confidence:** `[H]` with period="2y" and total_signals > 15. `[M]` otherwise.

---

## Tool 14: `get_scan_summary`

**When to call:** When user wants a narrative market overview rather than raw scan data.
Good for opening summaries, "what's the market setup today?", or briefing-style output.

**Parameters:** None

**Key response fields to extract:**
```
summary_text           → Full natural-language summary of today's scan
top_5_bullets[]        → Array of concise signal bullets for top 5 stocks
sector_context         → Which sectors dominate the MA Ketat signals today
market_assessment      → Overall market setup description (e.g., "broad compression")
scan_date              → When the underlying scan was performed
```

**Confidence:** `[M]` — narrative output; verify raw scan data for trading decisions.

---

## Tool 15: `scan_golden_cross`

**When to call:** User asks "find golden cross stocks", "buy the dip in uptrend", "oversold stocks in uptrend",
"dip buy setup", or any request for golden cross / stochastic oversold screening.
**Warning:** Takes 30–90 seconds due to bulk data download.

**Strategy logic:**
```
Golden cross confirmed:  SMA50 > SMA200  (long-term bullish structure)
Price above SMA200:      trend intact — SMA200 acts as stop anchor
Stochastic Slow %K < 25: temporarily oversold / pulled back
Stochastic %K >= %D OR fresh K>D crossover from oversold: momentum turning up
Volume >= 500K:          sufficient liquidity
RSI(14) < 50:            confirms pullback, not overbought

Stochastic Slow formula:
  Fast %K  = 100 * (Close - LowestLow_14) / (HighestHigh_14 - LowestLow_14)
  Slow %K  = SMA(fast_k, 3)    ← smoothed %K
  %D       = SMA(slow_k, 3)    ← signal line
```

**Parameters:**
```
stoch_threshold: number   — Stochastic %K oversold threshold (default 25.0; lower = stricter)
min_volume:      integer  — Minimum daily volume filter (default 500,000)
```

**Key response fields to extract:**
```
meta.scan_date                → Date the scan was run
meta.total_tickers_scanned    → Number of stocks processed
meta.total_signals_found      → Number of passing signals
meta.parameters.stoch_threshold → Threshold used
top_10[]                      → Top 10 ranked signals
  .ticker                     → IDX ticker symbol
  .close                      → Last price (IDR)
  .golden_cross_confirmed     → Boolean — SMA50 > SMA200
  .days_since_golden_cross    → Sessions since last SMA50>SMA200 crossover
  .fresh_golden_cross         → Boolean — crossover within last 10 sessions
  .sma50                      → 50-day SMA (IDR)
  .sma200                     → 200-day SMA (IDR)
  .stoch_k                    → Stochastic Slow %K (lower = more oversold)
  .stoch_d                    → Stochastic Slow %D (signal line)
  .stoch_oversold             → Boolean — K < stoch_threshold
  .rsi                        → RSI(14) reading
  .volume                     → Today's volume (shares)
  .distance_from_sma200_pct   → % distance of Close above SMA200
  .entry_zone                 → [low, high] entry range (IDR)
  .stop_loss                  → Stop-loss price anchored at SMA200 (IDR)
  .stop_loss_pct              → % stop distance from current close
  .confidence_score           → 0–100 signal strength score
  .rank                       → Rank by confidence score
  .prediction{}               → Embedded prediction (see fields below)
all_signals[]                 → Full list of all passing signals (same schema)
summary_text                  → Natural language overview of scan results
```

**Confidence scoring breakdown (0–100):**
```
Stochastic depth         25 pts  (lower %K = deeper oversold = better dip)
Golden cross freshness   20 pts  (≤5 sessions = 20 pts; ≤10 = 15; ≤20 = 10; older = 5)
Stochastic momentum      15 pts  (K >= D and still rising = 15; K >= D = 10)
RSI reading              15 pts  (30–45 ideal zone = 15; 25–55 = 10; else = 3)
Volume vs 20D avg        15 pts  (≥2× = 15; ≥1.5× = 12; ≥1× = 9; below = 5)
Distance above SMA200    10 pts  (≤5% = 10; ≤10% = 7; >10% = 3)
```

**Stop-loss anchor:**
```
Stop-loss = SMA200 - 1 tick  (one tick below the long-term trend support)
This is different from MA Ketat which anchors at SMA20.
```

**Usage pattern:**
```
1. Run scan_golden_cross with default params
2. Take top 3–5 by confidence_score
3. Run analyze_golden_cross on each for detailed confirmation
4. Use embedded prediction for entry parameters
```

**Confidence:** `[M]` — scanner uses daily close data, not real-time. Scan validity ~4 hours (cached).

---

## Tool 16: `get_top_golden_cross`

**When to call:** Faster alternative to `scan_golden_cross` when you just want the top ranked dip-buy signals.
Uses cached results if today's scan has already run; triggers a fresh scan if not.

**Parameters:** None

**Key response fields to extract:**
```
scan_date           → Date of the underlying scan
total_scanned       → Number of stocks processed
total_signals       → Total passing signals found
top_10[]            → Array of top 10 ranked signals (same schema as scan_golden_cross top_10)
summary_text        → Natural language overview
```

**Confidence:** `[M]` — if scan_date does not match today, treat as stale; re-run scan_golden_cross.

---

## Tool 17: `analyze_golden_cross`

**When to call:** After identifying a candidate from scan_golden_cross/get_top_golden_cross, OR when user
asks "is XXXX a golden cross setup?", "golden cross analysis for XXXX", or similar single-stock requests.

**Parameters:**
```
ticker: string
period: "1y" | "2y"   — default "1y". Use "2y" if stock has insufficient data for SMA200.
```

**Key response fields to extract:**
```
ticker                      → Normalised ticker symbol
close                       → Last price (IDR)
scan_date                   → Analysis date
golden_cross{}              → Golden cross status block
  .confirmed                → Boolean — SMA50 > SMA200 right now
  .sma50                    → SMA50 value (IDR)
  .sma200                   → SMA200 value (IDR)
  .days_since_cross         → Sessions since last SMA50>SMA200 crossover (null = no cross found)
  .fresh                    → Boolean — crossover within last 10 sessions
stochastic{}                → Stochastic slow readings
  .k                        → Slow %K value
  .d                        → %D signal line value
  .oversold                 → Boolean — K < 25
  .momentum_bullish         → Boolean — K >= D or fresh oversold K>D crossover
rsi                         → RSI(14) value
volume                      → Today's volume (shares)
vol_20d_avg                 → 20-day average volume
distance_from_sma200_pct    → % distance of Close above SMA200
passes_filters              → Boolean — passes all golden cross entry criteria
assessment                  → "PASS — ..." or "FAIL — [reason]"
signal{}                    → Present only if passes_filters=true (same as scan signal schema)
prediction{}                → Present only if passes_filters=true
  .horizon_days             → Forecast period (trading days)
  .direction                → "BULLISH"
  .strength                 → "STRONG" | "MEDIUM" | "WEAK"
  .expected_gain_pct        → [low%, high%] expected gain range
  .target_price             → [target_lo, target_hi] in IDR
  .stop_loss_pct            → % stop distance (negative)
  .reward_risk_ratio        → R:R ratio
  .confidence_pct           → 0–100 prediction confidence
  .rationale                → Plain-language explanation of signal drivers
```

**Golden Cross interpretation:**
```
confirmed=true + fresh=true  → Recently crossed — most timely entry
confirmed=true + fresh=false → Long-standing uptrend — still valid, less urgent
confirmed=false              → No golden cross — not a valid setup for this strategy

passes_filters=true          → Active dip-buy setup — use signal + prediction for entry
passes_filters=false         → Check assessment field for specific fail reason(s):
  "no golden cross"          → SMA50 ≤ SMA200 — wait for uptrend establishment
  "price below SMA200"       → Trend not intact — avoid
  "stochastic not oversold"  → No dip yet — monitor for pullback
  "falling knife risk"       → %K still declining — wait for stochastic momentum turn
  "insufficient liquidity"   → Volume too low — skip or use limit orders only
  "RSI not below 50"         → Not a confirmed pullback yet
```

**Confidence:** `[H]` if period="1y" with sufficient data. `[M]` if period="2y" only.

---

## MCP Chaining Strategy

### Full Analysis (8 calls)
```
1. get_market_overview()          → Market context backdrop
2. get_company_profile(ticker)    → Know what you're analyzing
3. get_stock_price(ticker)        → Price anchor
4. get_financials(ticker, "annual") → Annual ratios
5. get_financials(ticker, "quarterly") → Recent growth
6. get_technicals(ticker, "6mo") → Technical picture
7. get_broker_summary(ticker)    → Who's buying/selling
8. get_foreign_flow(ticker, "monthly") → Foreign positioning
9. get_stock_news(ticker, 10)    → Catalysts
```
*Calls 7-9 can run in parallel conceptually — present results together.*

### Full Analysis + MA Ketat Layer (11 calls)
Same as Full Analysis above, plus:
```
10. analyze_ticker(ticker, "6mo")  → MA Ketat signal check
11. run_backtest(ticker, "1y")     → Signal historical reliability
    → If signal_present=true: get_prediction(ticker, 7) for entry parameters
```
*Add these only when user asks for MA Ketat analysis, or when a valid MA Ketat signal is found.*

### Quick Analysis (5 calls)
```
1. get_stock_price(ticker)
2. get_financials(ticker, "annual")
3. get_technicals(ticker, "6mo")
4. get_foreign_flow(ticker, "daily")
5. get_stock_news(ticker, 5)
```

### Market Screening / "What to buy today" (3–6 calls)
```
1. get_market_overview()            → Market backdrop; abort if IHSG < -1%
2. get_scan_summary()               → Narrative overview of today's signals
3. get_top10()                      → Ranked top 10 MA Ketat candidates
   → For each top 3 candidates:
4. analyze_ticker(ticker, "6mo")    → Confirm signal + pass/fail check
5. get_prediction(ticker, 7)        → Entry parameters for confirmed signals
6. run_backtest(ticker, "1y")       → Optional: validate signal reliability
```
*Use scan_today instead of get_top10 when you want to adjust thresholds.*

### MA Ketat Deep Dive (single stock, 3 calls)
```
1. analyze_ticker(ticker, "6mo")   → Full MA Ketat assessment
2. get_prediction(ticker, 7)       → Short-term targets
3. run_backtest(ticker, "1y")      → Historical win rate
```

### Golden Cross Screening / "Buy the dip in uptrend" (3–5 calls)
```
1. get_market_overview()                    → Market backdrop; assess IHSG trend
2. get_top_golden_cross()                   → Ranked top 10 dip-buy candidates
   → For each top 3 candidates:
3. analyze_golden_cross(ticker, "1y")       → Confirm signal + pass/fail + prediction
   (prediction embedded in response; no separate tool call needed)
```
*Use scan_golden_cross instead of get_top_golden_cross when you want to adjust stoch_threshold or min_volume.*

### Golden Cross Deep Dive (single stock, 1–2 calls)
```
1. analyze_golden_cross(ticker, "1y")   → Full golden cross assessment + embedded prediction
   → If passes_filters=false: check assessment field for fail reasons
   → If passes_filters=true:  use signal + prediction from same response
2. (Optional) get_technicals(ticker, "6mo") → Cross-check RSI/MACD from standard tech tool
```

### Full Analysis + Golden Cross Layer (10 calls)
Same as Full Analysis (calls 1–9), plus:
```
10. analyze_golden_cross(ticker, "1y")  → Golden cross signal check
    → If passes_filters=true: present dip-buy entry parameters from embedded prediction
```
*Add only when user asks for golden cross analysis, or when SMA50>SMA200 is confirmed in get_technicals.*

### Price Check Only (2 calls)
```
1. get_stock_price(ticker)
2. get_market_overview()    → IHSG context
```

---

## Error Handling

| Error | Action |
|---|---|
| Tool returns null/empty | Mark field as `[N/A]`, use `[L]` confidence, web search as fallback |
| Ticker not found | Try ticker + ".JK" suffix or search for correct ticker spelling |
| Stale data (>7 days) | Downgrade confidence to `[L]`, note date in output |
| Partial data (some fields null) | Compute what you can, skip unavailable ratios, note gaps |
| MCP server unavailable | Fall back entirely to web search, note "IDX-MCP unavailable" in sources |

---

## Supplementary Web Search (use only when MCP gaps exist)

| Missing Data | Search Query |
|---|---|
| Analyst price target | `[TICKER] IDX analyst rating price target [year]` |
| PEG ratio / forward P/E | `[TICKER] earnings estimate forward PE IDX` |
| Detailed ownership | `[TICKER] pemegang saham majority shareholder IDX` |
| Commodity sensitivity | `[SECTOR] coal/CPO/nickel price impact revenue sensitivity` |
| Corporate action detail | `[TICKER] corporate action rights issue dividend announcement` |
| Bandarmology detail | `[TICKER] bandarmology accumulation distribution stockbit` |
