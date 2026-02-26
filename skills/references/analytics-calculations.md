# Analytics & Calculations Reference

Every formula used in StockAnalyst Pro. When MCP returns raw data without pre-computed
ratios, use these to derive them. Always show the formula used alongside the result.

---

## 1. VALUATION RATIOS

### P/E Ratio (Price-to-Earnings)
```
TTM P/E  = price / (ttm_net_income / shares_outstanding)
         = price / ttm_eps

Forward P/E = price / forward_eps_estimate

Where TTM = trailing 12 months = sum of last 4 quarterly net incomes
shares_outstanding = total_equity / book_value_per_share  [if shares not directly given]

Flag: If net_income is negative → P/E = undefined → write "N/M (loss)" → Score 1 on value factor
```

### P/B Ratio (Price-to-Book)
```
P/B = price / book_value_per_share
    = price / (total_equity / shares_outstanding)
    = market_cap / total_equity

For banks: Tangible P/B = market_cap / (total_equity - intangible_assets - goodwill)
```

### EV/EBITDA
```
Enterprise Value (EV) = market_cap + total_debt - cash_and_equivalents
EBITDA                = operating_income + depreciation + amortization
                      = net_income + interest_expense + tax_expense + D&A

EV/EBITDA = EV / EBITDA

If EBITDA not in MCP response, approximate:
EBITDA ≈ gross_profit - operating_expenses + D&A   [if D&A available]
EBITDA ≈ operating_income × 1.15                  [rough approximation if D&A unavailable]
Flag approximation with [est]
```

### Dividend Yield
```
Dividend Yield = (annual_dividend_per_share / price) × 100

If only payout ratio available:
Dividend Yield = (eps × payout_ratio / price) × 100

IDX note: Most Indonesian companies pay annual dividends after AGM (typically April–June).
Ex-date typically 1–3 months after announcement.
```

### PEG Ratio
```
PEG = pe_ratio / annual_eps_growth_rate_percent

Example: P/E = 15, EPS growth = 20% → PEG = 15/20 = 0.75

If using forward growth estimate: PEG = forward_pe / projected_eps_growth
Flag any PEG using estimated growth with [est]
```

### EV/Sales
```
EV/Sales = enterprise_value / ttm_revenue
Use when EBITDA is negative (high-growth/loss-making companies like tech, GOTO)
Score: < 1x = cheap, 1–3x = fair, > 5x = expensive (for profitable cos)
```

---

## 2. PROFITABILITY & EFFICIENCY RATIOS

### ROE (Return on Equity)
```
ROE = net_income / average_shareholders_equity × 100
    = net_income / ((equity_start + equity_end) / 2) × 100

Simplified (if only year-end equity available):
ROE = net_income / total_equity × 100

DuPont decomposition (for deeper analysis if components available):
ROE = Net Margin × Asset Turnover × Equity Multiplier
    = (net_income/revenue) × (revenue/assets) × (assets/equity)
```

### ROA (Return on Assets)
```
ROA = net_income / average_total_assets × 100
    = net_income / total_assets × 100  [simplified]
```

### ROCE (Return on Capital Employed)
```
ROCE = operating_income / (total_assets - current_liabilities) × 100
Capital Employed = total_assets - current_liabilities
```

### Net Profit Margin
```
Net Margin = net_income / revenue × 100
```

### Gross Profit Margin
```
Gross Margin = gross_profit / revenue × 100
             = (revenue - cogs) / revenue × 100
```

### EBITDA Margin
```
EBITDA Margin = ebitda / revenue × 100
```

### Asset Turnover
```
Asset Turnover = revenue / average_total_assets
```

### Receivables Days (DSO)
```
DSO = (accounts_receivable / revenue) × 365
High DSO in property/construction = cash flow risk
```

### Interest Coverage
```
Interest Coverage = operating_income / interest_expense
                  = ebit / interest_expense
Healthy: > 5x. Stress: < 2x. Crisis: < 1x (can't cover interest from operations)
```

### FCF (Free Cash Flow)
```
FCF = operating_cash_flow - capital_expenditure
FCF Yield = FCF / market_cap × 100
FCF Margin = FCF / revenue × 100

If cash flow statement not available, approximate:
FCF ≈ net_income + depreciation - capex - change_in_working_capital
```

---

## 3. LEVERAGE & FINANCIAL HEALTH

### Debt-to-Equity
```
D/E = total_debt / total_equity
where total_debt = short_term_debt + long_term_debt  [interest-bearing only, not all liabilities]
```

### Net Debt / EBITDA
```
Net Debt = total_debt - cash_and_equivalents
Net Debt/EBITDA = net_debt / ebitda

Healthy: < 2x. Caution: 2–4x. Stress: > 4x.
```

### Current Ratio
```
Current Ratio = current_assets / current_liabilities
```

### Quick Ratio (Acid Test)
```
Quick Ratio = (current_assets - inventory) / current_liabilities
Stricter liquidity test — useful for manufacturing, retail, commodity companies.
```

### Net Gearing
```
Net Gearing = net_debt / total_equity × 100
```

### Banking-Specific: CAR
```
Capital Adequacy Ratio = (tier1_capital + tier2_capital) / risk_weighted_assets × 100
OJK minimum: 8%. Healthy bank in Indonesia: >15%
```

### Banking-Specific: NIM
```
Net Interest Margin = net_interest_income / average_earning_assets × 100
Healthy IDX bank: 4–7%
```

### Banking-Specific: NPL
```
Non-Performing Loan Ratio = gross_NPL / total_loans × 100
OJK threshold: < 5% (regulatory concern). Well-managed: < 2%
```

---

## 4. GROWTH METRICS

### YoY Revenue Growth
```
Revenue Growth = (revenue_current_period / revenue_prior_period) - 1
Use same quarter comparison (Q1 vs Q1) to avoid seasonality distortion
```

### YoY EPS Growth
```
EPS Growth = (eps_current / eps_prior) - 1
For TTM: sum last 4 quarters vs prior 4 quarters
```

### Revenue CAGR (3-year)
```
3Y Revenue CAGR = (revenue_year_3 / revenue_year_0) ^ (1/3) - 1
```

### Earnings CAGR (3-year)
```
3Y EPS CAGR = (eps_year_3 / eps_year_0) ^ (1/3) - 1
Note: Skip if any base year has negative EPS (CAGR undefined for negative base)
```

---

## 5. TECHNICAL CALCULATIONS

### Moving Average Relationship
```
Price vs MA = (price / ma_value) - 1   [positive = above, negative = below]

MA trend:
  EMA20 > SMA50 > SMA200 = strong uptrend (aligned bulls)
  EMA20 < SMA50 < SMA200 = strong downtrend (aligned bears)
  Mixed = consolidation/transition phase
```

### Golden Cross / Death Cross
```
Golden Cross = SMA50 crosses above SMA200  → Bullish structural signal
Death Cross  = SMA50 crosses below SMA200  → Bearish structural signal
```

### MACD Interpretation
```
MACD Line    = EMA12 - EMA26
Signal Line  = EMA9 of MACD Line
Histogram    = MACD Line - Signal Line

Histogram > 0 and growing   → Accelerating bullish momentum
Histogram > 0 and shrinking → Bullish momentum fading
Histogram < 0 and shrinking → Bearish momentum fading (potential reversal)
Histogram < 0 and growing   → Accelerating bearish momentum

Bullish cross: MACD crosses above signal line
Bearish cross: MACD crosses below signal line
```

### RSI Divergence
```
Bullish divergence: Price making lower lows, RSI making higher lows → Potential reversal up
Bearish divergence: Price making higher highs, RSI making lower highs → Potential reversal down
```

### Support / Resistance Levels
```
From MCP: use support_1, support_2, support_3, resistance_1, resistance_2, resistance_3

If not provided, derive:
  Nearest support    = recent swing low (visually identified)
  Strong support     = high-volume consolidation zone
  Resistance         = prior highs where price reversed

Distance calculation: (price - support) / price × 100  → % to support
                      (resistance - price) / price × 100 → % to resistance
```

### Volume Analysis
```
Volume signal = today_volume / volume_avg_20d

Breakout confirmation: price break + volume > 1.5× avg → high conviction signal
Distribution: price rises + volume declining → weakening demand
Climax volume: volume > 3× avg → potential exhaustion (at highs) or capitulation (at lows)
```

### ARA / ARB Price Limits (IDX)
```
Standard stocks:
ARA = price × 1.35
ARB = price × 0.65

Newly listed stocks (IPO, first 5 sessions):
ARA = price × 1.35 × 2 = ×2.70 (for some categories)

Special monitoring board (papan pemantauan khusus):
ARA = price × 1.10
ARB = price × 0.90
```

---

## 6. TRADE PARAMETER CALCULATIONS

### Entry Zone
```
Conservative entry = support_1 to (support_1 × 1.02)    [2% above support]
Aggressive entry   = current_price to (current_price × 1.03)  [chase up to 3%]
Optimal entry      = EMA20 ± 1%  [on pullback to short-term MA]
```

### Stop Loss
```
Technical stop   = support_1 × 0.97    [3% below nearest support]
ATR-based stop   = price - (2 × atr)   [requires ATR — use if available]
Percentage stop  = price × 0.90        [10% hard stop, default if no clear support]

For illiquid stocks: widen stop by 1.5×
For high-beta stocks (>1.4): widen stop by 1.3×
```

### Take Profit Levels
```
TP1 = resistance_1                          [nearest resistance, ~50% of position]
TP2 = resistance_2                          [second resistance, ~30% of position]
TP3 = resistance_3 OR price × 1.30         [extended target, remaining 20%]
```

### Risk-to-Reward Ratio
```
R:R = (TP1 - entry) / (entry - stop_loss)
Minimum acceptable R:R = 1.5:1
Target R:R = 2:1 or better
```

### Position Sizing
```
Standard formula:
Position size % = (risk_per_trade_% × portfolio) / (entry - stop_loss)

Default risk per trade = 2% of portfolio
If R:R > 3:1 → increase to 3%
If R:R < 1.5:1 → reduce to 1% or skip

IDX adjustments:
  LQ45/IDX30 stock → standard sizing
  Mid-cap → reduce by 25%
  Small-cap or low liquidity → reduce by 50%
  ARA/ARB → reduce by 50%
  BUMN → standard (liquidity backstop)
```

---

## 7. SCENARIO ANALYSIS CALCULATIONS

### Bull / Base / Bear Targets
```
Bull target  = nearest major resistance × 1.05 to 1.15  [breakout continuation]
Base target  = TP2 from trade parameters                  [resistance 2]
Bear target  = support_2 OR (entry - 2 × (entry - stop)) [2R loss scenario]
```

### Scenario Probability-Weighted Return
```
Expected Return = (P_bull × return_bull) + (P_base × return_base) + (P_bear × return_bear)

Standard probability weights:
  Bull: 25%  (optimistic but plausible)
  Base: 50%  (most likely outcome)
  Bear: 25%  (pessimistic but plausible)

Return = (target_price / entry_price) - 1

Example:
  Bull +40% × 25% = +10.0%
  Base +15% × 50% = +7.5%
  Bear -20% × 25% = -5.0%
  Expected Return  = +12.5%
```

---

## 8. SECTOR-SPECIFIC METRICS

### Coal Mining (PTBA, ITMG, ADRO, INDY, BYAN)
```
Revenue sensitivity: ~1:1 with coal price (Newcastle thermal)
Key: ASP (Average Selling Price) per ton, production volume (MT)
Coal price break-even = COGS per ton / ASP × current_price → revenue sensitivity

EBITDA per ton = EBITDA / production_volume_tons
Stripping ratio = waste removed / coal produced (higher = more expensive mining)
```

### CPO / Plantations (AALI, SIMP, BWPT, LSIP)
```
Revenue sensitivity: ~0.8:1 with CPO price (MYR/ton)
Key: CPO production volume (MT), nucleus vs plasma plantation ratio
CPO yield per hectare = production / planted_area
```

### Banking (BBCA, BBRI, BMRI, BBNI, BRIS)
```
NIM (Net Interest Margin)      = net_interest_income / earning_assets × 100
CASA Ratio                     = (current_account + savings) / total_deposits × 100
LDR (Loan-to-Deposit Ratio)    = total_loans / total_deposits × 100
CAR                            = capital / risk_weighted_assets × 100
NPL Gross                      = gross_NPL / total_loans × 100
NPL Net                        = net_NPL / total_loans × 100
Coverage Ratio                 = loan_loss_provisions / gross_NPL × 100
Credit Cost                    = provision_expense / average_loans × 100
```

### Property (BSDE, CTRA, SMRA, PWON)
```
Revenue recognition: based on handover, not booking → lag vs presales
Key: Presales target (IDR T), presales achievement %, landbank (ha)
NAV = (market_value_of_properties - total_debt) / shares_outstanding
NAV premium/discount = (price / NAV) - 1
Recurring vs one-time revenue split (recurring = rental, hotel, mall)
```

### Telecom (TLKM, EXCL, ISAT)
```
ARPU (Average Revenue Per User) = monthly_revenue / average_subscribers
Churn rate = subscribers_lost / average_subscribers × 100
Data revenue share = data_revenue / total_revenue × 100
EBITDA margin target: telcos typically 45–55%
Capex intensity = capex / revenue × 100
```

### Consumer / Retail (ICBP, INDF, UNVR, MYOR)
```
Same-store sales growth (SSSG) = best indicator of organic growth
Gross margin trend = key indicator of pricing power vs raw material costs
Distribution reach / SKU count
Advertising spend as % of revenue
```
