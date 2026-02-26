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

### Quick Analysis (5 calls)
```
1. get_stock_price(ticker)
2. get_financials(ticker, "annual")
3. get_technicals(ticker, "6mo")
4. get_foreign_flow(ticker, "daily")
5. get_stock_news(ticker, 5)
```

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
