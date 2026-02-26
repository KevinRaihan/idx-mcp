# Factor Scoring Reference

Complete 6-factor quantitative scoring engine. Score every factor 1–5 from MCP data.
Final composite = weighted average. Always show raw metric AND score.

---

## Factor Weights

```
Factor 1: Momentum          20%
Factor 2: Value             20%
Factor 3: Quality           25%   ← highest weight: profitability is primary alpha driver
Factor 4: Low Volatility    10%
Factor 5: Liquidity         15%   ← IDX-critical, gate function
Factor 6: Flow & Sentiment  10%
Total                      100%
```

**Composite Score → Rating:**
```
4.0 – 5.0  →  STRONG BUY   ★★★★★
3.3 – 3.9  →  BUY          ★★★★
2.7 – 3.2  →  HOLD         ★★★
2.0 – 2.6  →  SELL         ★★
< 2.0      →  STRONG SELL  ★
```

---

## ⚠️ LIQUIDITY GATE (Apply Before Composite)

**Rule:** If `avg_daily_traded_value < IDR 1,000,000,000 (1B)` → Cap composite at 2.5
regardless of other factor scores. Apply this warning block:

```
⚠️ LIQUIDITY WARNING
Avg daily value: IDR [X]M — below IDR 1B threshold.
Composite score capped at 2.5. Treat as speculative only.
Position sizing: max 1% of portfolio.
Execution: limit orders only. Widen stop-loss by 1.5×.
Exit: may require multiple sessions to build/unwind position.
```

Additional gates:
- `avg_daily_value < IDR 100M` → Add `⛔ ILLIQUID — Do not enter`
- `free_float < 5%` → Add `⛔ NEAR-ZERO FLOAT — Manipulation risk high`

---

## FACTOR 1 — MOMENTUM (20%)

**Source:** `get_technicals` + `get_stock_price`
**Data needed:** Current price, price 3 months ago, price 6 months ago, IHSG 3M return

**Scoring Table:**

### 1A. 3-Month Price Return
```
Calculation: (price_now / price_3m_ago) - 1
Score 5: > +15%
Score 4: +5% to +15%
Score 3: -5% to +5%
Score 2: -15% to -5%
Score 1: < -15%
```

### 1B. 6-Month Price Return
```
Calculation: (price_now / price_6m_ago) - 1
Score 5: > +25%
Score 4: +10% to +25%
Score 3: -10% to +10%
Score 2: -25% to -10%
Score 1: < -25%
```

### 1C. Relative Momentum vs IHSG (3M)
```
Calculation: stock_3m_return - ihsg_3m_return
Score 5: > +10 percentage points
Score 4: 0 to +10pp
Score 3: -5pp to 0pp
Score 2: -15pp to -5pp
Score 1: < -15pp
```

### 1D. RSI Signal
```
Source: get_technicals → rsi field
Score 5: 50–65   (bullish momentum, not overbought)
Score 4: 40–50 OR 65–70
Score 3: 35–40 OR 70–75
Score 2: 30–35 OR 75–80
Score 1: <30 (oversold — momentum broken) OR >80 (extreme overbought)
```

**Factor 1 score = average(1A, 1B, 1C, 1D)**

---

## FACTOR 2 — VALUE (20%)

**Source:** `get_financials` (annual) — pre-computed ratios or derived from statements
**Data needed:** P/E, P/B, EV/EBITDA, dividend yield, PEG

### 2A. P/E Ratio (TTM)
```
Source: pe_ratio field OR compute: price / (ttm_net_income / shares_outstanding)
Score 5: < 8
Score 4: 8 to 15
Score 3: 15 to 22
Score 2: 22 to 30
Score 1: > 30 OR negative earnings

IDX context: Market average P/E typically 10–18x. Adjust expectations by sector.
```

### 2B. P/B Ratio
```
Source: pb_ratio field OR compute: price / (total_equity / shares_outstanding)
Score 5: < 1.0
Score 4: 1.0 to 1.5
Score 3: 1.5 to 2.5
Score 2: 2.5 to 4.0
Score 1: > 4.0

BANKING OVERRIDE: P/B < 1.2 = Score 5; 1.2–2.0 = Score 4; > 2.0 = Score 3 or below
(Banks trade on book value, higher P/B acceptable if ROE > 18%)
```

### 2C. EV/EBITDA
```
Source: ev_ebitda field OR compute: (market_cap + total_debt - cash) / ebitda
Score 5: < 5x
Score 4: 5x to 8x
Score 3: 8x to 12x
Score 2: 12x to 18x
Score 1: > 18x OR negative EBITDA

PROPERTY OVERRIDE: Use NAV discount/premium instead. Premium > 50% = Score 1.
NAV at par = Score 3. Discount > 30% = Score 5.
```

### 2D. Dividend Yield
```
Source: dividend_yield field OR compute: (annual_dividend_per_share / price) × 100
Score 5: > 6%
Score 4: 4% to 6%
Score 3: 2% to 4%
Score 2: 1% to 2%
Score 1: < 1% or no dividend
```

### 2E. PEG Ratio
```
Calculation: pe_ratio / (eps_growth_rate_percent)
Score 5: < 0.8   (growth available cheap)
Score 4: 0.8 to 1.2
Score 3: 1.2 to 2.0
Score 2: 2.0 to 3.0
Score 1: > 3.0 OR negative growth rate (PEG undefined)
```

**Factor 2 score = average(2A, 2B, 2C, 2D, 2E)**
*Skip PEG (2E) and use average of remaining 4 if EPS growth data unavailable.*

---

## FACTOR 3 — QUALITY (25%)

**Source:** `get_financials` — both annual and quarterly
**Data needed:** ROE, ROA, margins, D/E, current ratio, EPS growth, revenue growth

### 3A. ROE (Return on Equity)
```
Source: roe field OR compute: net_income / total_equity × 100
Score 5: > 20%
Score 4: 15% to 20%
Score 3: 10% to 15%
Score 2: 5% to 10%
Score 1: < 5% or negative

BANKING OVERRIDE: > 18% = Score 5; 15–18% = Score 4; 12–15% = Score 3; < 12% = Score 2
```

### 3B. ROA (Return on Assets)
```
Source: roa field OR compute: net_income / total_assets × 100
Score 5: > 10%
Score 4: 7% to 10%
Score 3: 4% to 7%
Score 2: 2% to 4%
Score 1: < 2% or negative

BANKING OVERRIDE: ROA > 2% = Score 5 (banks are inherently high-leverage)
```

### 3C. Net Profit Margin
```
Source: net_margin field OR compute: net_income / revenue × 100
Score 5: > 20%
Score 4: 12% to 20%
Score 3: 6% to 12%
Score 2: 2% to 6%
Score 1: < 2% or negative
```

### 3D. Debt-to-Equity Ratio
```
Source: debt_to_equity field OR compute: total_debt / total_equity
Score 5: < 0.3
Score 4: 0.3 to 0.7
Score 3: 0.7 to 1.5
Score 2: 1.5 to 3.0
Score 1: > 3.0

BANKING OVERRIDE: Use Capital Adequacy Ratio (CAR) instead:
CAR > 20% = Score 5; 18–20% = Score 4; 15–18% = Score 3; 12–15% = Score 2; < 12% = Score 1

INFRASTRUCTURE/TOLL ROAD OVERRIDE: D/E up to 2.5 acceptable (regulated revenues)
D/E < 1.5 = Score 5; 1.5–2.5 = Score 4; 2.5–4.0 = Score 3; > 4.0 = Score 1
```

### 3E. Current Ratio
```
Source: current_ratio field OR compute: current_assets / current_liabilities
Score 5: > 2.5
Score 4: 1.8 to 2.5
Score 3: 1.2 to 1.8
Score 2: 0.8 to 1.2
Score 1: < 0.8 (liquidity stress risk)

BANKING OVERRIDE: Not applicable — use NPL ratio instead:
NPL < 1% = Score 5; 1–2% = Score 4; 2–3% = Score 3; 3–5% = Score 2; > 5% = Score 1
```

### 3F. EPS YoY Growth
```
Source: quarterly financials — compare latest quarter EPS vs same quarter prior year
Calculation: (eps_latest_q / eps_same_q_prior_year) - 1
Score 5: > 30%
Score 4: 15% to 30%
Score 3: 5% to 15%
Score 2: 0% to 5%
Score 1: Negative (earnings declining)
```

### 3G. Revenue YoY Growth
```
Source: quarterly financials — compare latest quarter revenue vs same quarter prior year
Calculation: (rev_latest_q / rev_same_q_prior_year) - 1
Score 5: > 25%
Score 4: 12% to 25%
Score 3: 5% to 12%
Score 2: 0% to 5%
Score 1: Negative (revenue declining)
```

**Factor 3 score = average(3A, 3B, 3C, 3D, 3E, 3F, 3G)**
*Skip banking-NA metrics and average remaining.*

---

## FACTOR 4 — LOW VOLATILITY (10%)

**Source:** `get_technicals` (6mo period)
**Data needed:** Price history for CV calculation, drawdown, beta if available

### 4A. Coefficient of Variation (6M)
```
Calculation: price_std_deviation_6m / price_mean_6m
(If not directly in MCP: approximate from 52W high/low range)
Approximation: (52w_high - 52w_low) / ((52w_high + 52w_low) / 2) / 2
Score 5: < 0.10
Score 4: 0.10 to 0.20
Score 3: 0.20 to 0.35
Score 2: 0.35 to 0.50
Score 1: > 0.50
```

### 4B. Maximum Drawdown (6M)
```
Calculation: (price_now / 6m_high) - 1   [negative number]
Score 5: Better than -5%
Score 4: -5% to -15%
Score 3: -15% to -25%
Score 2: -25% to -35%
Score 1: Worse than -35%
```

### 4C. Beta vs IHSG
```
Source: web search or TradingView if not in MCP
Score 5: < 0.7   (defensive)
Score 4: 0.7 to 0.9
Score 3: 0.9 to 1.1
Score 2: 1.1 to 1.4
Score 1: > 1.4   (highly volatile)
Skip if unavailable, average remaining two.
```

**Factor 4 score = average(4A, 4B, 4C)**

---

## FACTOR 5 — LIQUIDITY / MARKETABILITY (15%)

**Source:** `get_stock_price` (value field), `get_company_profile` (free_float, foreign_ownership)
**⚠️ This factor governs the Liquidity Gate above.**

### 5A. Average Daily Traded Value
```
Source: value field from get_stock_price (today's value)
For more accurate avg: note if today seems outlier vs typical
Score 5: > IDR 50 billion
Score 4: IDR 10B to 50B
Score 3: IDR 1B to 10B
Score 2: IDR 100M to 1B      ← LIQUIDITY GATE triggers here and below
Score 1: < IDR 100 million
```

### 5B. Free Float Percentage
```
Source: free_float_percent from get_company_profile
Score 5: > 40%
Score 4: 25% to 40%
Score 3: 15% to 25%
Score 2: 5% to 15%
Score 1: < 5%
```

### 5C. Foreign Ownership Percentage
```
Source: major_shareholders or foreign_ownership from get_company_profile
Score 5: > 20%   (high foreign interest → eligible for global fund mandates)
Score 4: 10% to 20%
Score 3: 5% to 10%
Score 2: 1% to 5%
Score 1: < 1%   (no foreign interest)
```

**Factor 5 score = average(5A, 5B, 5C)**

---

## FACTOR 6 — FLOW & SENTIMENT (10%)

**Source:** `get_foreign_flow`, `get_broker_summary`, `get_stock_news`

### 6A. Net Foreign Flow (30-day)
```
Source: get_foreign_flow(ticker, period="monthly")
Score 5: Strong net buy (> +50B IDR or > +1% of market cap)
Score 4: Mild net buy (+10B to +50B)
Score 3: Neutral (-10B to +10B)
Score 2: Mild net sell (-50B to -10B)
Score 1: Strong net sell (< -50B or < -1% of market cap)
```

### 6B. Institutional Broker Net Position
```
Source: get_broker_summary — classify using broker code table in idx-mcp-guide.md
Score 5: 2+ foreign institutional brokers among top 3 buyers, net positive
Score 4: Domestic institutional brokers accumulating (CC, DX, BK, LG net buying)
Score 3: Mixed/neutral — balanced buy/sell across broker types
Score 2: Domestic institutional distributing
Score 1: Foreign institutional brokers among top sellers, net negative
```

### 6C. Analyst & News Consensus
```
Source: get_stock_news + web search for analyst ratings
Score 5: 3+ recent positive catalysts OR Strong Buy consensus
Score 4: 1-2 positive catalysts OR Buy consensus
Score 3: Neutral news OR Hold consensus
Score 2: 1-2 negative catalysts OR Sell consensus
Score 1: 3+ negative catalysts OR Strong Sell consensus
```

**Factor 6 score = average(6A, 6B, 6C)**

---

## Sector-Specific Override Summary

| Sector | Metric | Override |
|---|---|---|
| Banking | D/E → CAR | CAR >20%=5, 18–20%=4, 15–18%=3, 12–15%=2, <12%=1 |
| Banking | Current Ratio → NPL | NPL <1%=5, 1–2%=4, 2–3%=3, 3–5%=2, >5%=1 |
| Banking | ROA threshold | >2%=5 (vs normal >10%=5) |
| Banking | P/B threshold | Up to 2.0 acceptable if ROE >18% |
| Property | EV/EBITDA → NAV discount | Discount >30%=5, at par=3, Premium >50%=1 |
| Commodity | Value & Quality | Score relative to commodity cycle phase, not absolute |
| Infrastructure | D/E threshold | Up to 2.5 acceptable; Score 5 if D/E <1.5 |
| Holding Co | EV/EBITDA → SOTP discount | Use sum-of-parts NAV; discount >30%=5 |

---

## Special Flags (Auto-trigger)

### ARA/ARB Flag
```
Trigger: Stock has hit ARA or ARB limit in the last 3 trading sessions
Output:
⛔ ARA/ARB ALERT
Stock has hit [ARA/ARB] on [N] of last 3 sessions.
Max position size: halved to [X%] of portfolio.
Spreads may be locked. Entry/exit extremely difficult.
Risk: price dislocation upon resumption of normal trading.
```

### BUMN Flag
```
Trigger: is_bumn = true in company profile
Output:
⚑ BUMN — Government equity: [X]%
Political cycle risk: Management/strategy may change post-election.
Mandatory dividend policy: typically ≥30% payout ratio.
Positive: government support backstop; access to state contracts.
Negative: efficiency drag; priority may be political, not shareholder return.
```

### Conglomerate Subsidiary Flag
```
Trigger: conglomerate_group is present in profile
Output:
🏢 [GROUP NAME] Conglomerate
Parent group health: [assess from available news/financials]
Related-party transaction risk: [high/medium/low based on group profile table]
Cross-holding complexity: [note if stock is also held by group sisters]
```

### Commodity-Linked Flag
```
Trigger: Sector = Mining, Agriculture, or Basic Industry
Output: Add row to scenario table:
Commodity sensitivity: [+/-X% in stock price per +/-10% in [commodity]]
Bull commodity assumption: [price]
Base commodity assumption: [price]
Bear commodity assumption: [price]
```
