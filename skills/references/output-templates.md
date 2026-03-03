# Output Templates

Exact copy-paste templates for every report section.
Replace `[PLACEHOLDER]` values with real data. Remove rows marked `[skip if N/A]`.

---

## TEMPLATE 0 — Report Header Block
*(Required on every report)*

```
📊 [TICKER] — [Full Company Name]
[Sector] | [Sub-sector]  |  [Session status: OPEN Session 1 / BREAK / OPEN Session 2 / CLOSED]

Price:   IDR [X,XXX]  ([+/-X.X%] today)  |  as of [HH:MM WIB, DD-MMM-YYYY]
Mkt Cap: IDR [X.X]Tr  |  Float: [X]%  |  Avg Daily Value: IDR [X]B
Indices: [LQ45 ✓] [IDX30 ✓] [ISSI ✓] [IDX80 ✓]  — or  [None]
BUMN:    [Yes – Govt [X]%]  — or  [No]
Group:   [Conglomerate name]  — or  [Independent]

COMPOSITE SCORE:  [X.X] / 5.0  →  [STRONG BUY / BUY / HOLD / SELL / STRONG SELL]
Data Confidence:  [H / M / L]
```

---

## TEMPLATE 1 — Factor Scorecard
*(Step 2 output — always include)*

```
╔══════════════════════════════════════════════════════════════════════════╗
║  FACTOR SCORECARD                                                        ║
╠══════════════════╦════════╦═══════════════════════════════╦═══════╦══════╣
║ Factor           ║ Weight ║ Key Metrics (raw values)       ║ Score ║ Wtd  ║
╠══════════════════╬════════╬═══════════════════════════════╬═══════╬══════╣
║ 1. Momentum      ║  20%   ║ 3M: [+X%] | 6M: [+X%]        ║       ║      ║
║                  ║        ║ vs IHSG: [+Xpp] | RSI: [XX]   ║  X.X  ║ X.XX ║
╠══════════════════╬════════╬═══════════════════════════════╬═══════╬══════╣
║ 2. Value         ║  20%   ║ P/E: [XX]x | P/B: [X.X]x     ║       ║      ║
║                  ║        ║ EV/EBITDA: [X]x | Yield: [X]% ║  X.X  ║ X.XX ║
╠══════════════════╬════════╬═══════════════════════════════╬═══════╬══════╣
║ 3. Quality       ║  25%   ║ ROE: [X]% | Margin: [X]%      ║       ║      ║
║                  ║        ║ D/E: [X.X]x | EPS growth: [X]%║  X.X  ║ X.XX ║
╠══════════════════╬════════╬═══════════════════════════════╬═══════╬══════╣
║ 4. Low Vol       ║  10%   ║ Max DD: [X]% | CV: [X.X]      ║       ║      ║
║                  ║        ║ Beta: [X.X] (vs IHSG)          ║  X.X  ║ X.XX ║
╠══════════════════╬════════╬═══════════════════════════════╬═══════╬══════╣
║ 5. Liquidity     ║  15%   ║ Avg Value: IDR [X]B/day        ║       ║      ║
║                  ║        ║ Float: [X]% | Foreign: [X]%    ║  X.X  ║ X.XX ║
╠══════════════════╬════════╬═══════════════════════════════╬═══════╬══════╣
║ 6. Flow          ║  10%   ║ Foreign flow 30D: [+/-IDR XB]  ║       ║      ║
║                  ║        ║ Broker: [accum/distrib/neutral] ║  X.X  ║ X.XX ║
╠══════════════════╬════════╬═══════════════════════════════╬═══════╬══════╣
║ COMPOSITE        ║ 100%   ║                                ║       ║ X.XX ║
╚══════════════════╩════════╩═══════════════════════════════╩═══════╩══════╝
Rating: [STRONG BUY / BUY / HOLD / SELL / STRONG SELL]
```

---

## TEMPLATE 2 — Technical Snapshot
*(Step 3 output)*

```
┌─────────────────────────────────────────────────────────────────────┐
│  TECHNICAL SNAPSHOT  —  [TICKER]  |  [DD-MMM-YYYY]                  │
├──────────────────────┬──────────────┬─────────────────────────────┤
│ Indicator            │ Value        │ Signal                      │
├──────────────────────┼──────────────┼─────────────────────────────┤
│ Price                │ IDR [X,XXX]  │ —                           │
│ vs EMA 20            │ [+/-X.X%]    │ [Above ▲ / Below ▼]        │
│ vs SMA 50            │ [+/-X.X%]    │ [Above ▲ / Below ▼]        │
│ vs SMA 200           │ [+/-X.X%]    │ [Above ▲ / Below ▼]        │
│ MA Alignment         │ [EMA>50>200] │ [Uptrend / Downtrend / Mix]│
├──────────────────────┼──────────────┼─────────────────────────────┤
│ RSI (14)             │ [XX.X]       │ [Oversold/Neutral/Overbought]│
│ MACD Line / Signal   │ [X.X / X.X]  │ [Bullish / Bearish cross]  │
│ MACD Histogram       │ [+/-X.X]     │ [Growing / Shrinking]      │
│ Stochastic %K/%D     │ [XX / XX]    │ [OS/Neutral/OB]            │
├──────────────────────┼──────────────┼─────────────────────────────┤
│ Volume today         │ [X.XM shrs]  │ [+X% vs 20D avg]           │
│ Avg daily vol (20D)  │ [X.XM shrs]  │ —                          │
├──────────────────────┼──────────────┼─────────────────────────────┤
│ 52W High             │ IDR [X,XXX]  │ [X.X% below high]          │
│ 52W Low              │ IDR [X,XXX]  │ [+X.X% above low]          │
├──────────────────────┼──────────────┼─────────────────────────────┤
│ Support 1            │ IDR [X,XXX]  │ [X.X% downside]            │
│ Support 2            │ IDR [X,XXX]  │ [X.X% downside]            │
│ Support 3            │ IDR [X,XXX]  │ [X.X% downside]            │
│ Resistance 1         │ IDR [X,XXX]  │ [X.X% upside]              │
│ Resistance 2         │ IDR [X,XXX]  │ [X.X% upside]              │
│ Resistance 3         │ IDR [X,XXX]  │ [X.X% upside]              │
├──────────────────────┼──────────────┼─────────────────────────────┤
│ Chart Pattern        │ [Name/None]  │ [Bullish/Bearish/Neutral]  │
│ ARA Limit            │ IDR [X,XXX]  │ [+35% upside ceiling]      │
│ ARB Limit            │ IDR [X,XXX]  │ [-35% downside floor]      │
└──────────────────────┴──────────────┴─────────────────────────────┘
Overall Technical Signal: [BULLISH / NEUTRAL / BEARISH]
```

---

## TEMPLATE 3 — Fundamental Snapshot
*(Step 4 output — three tables)*

**3A. Valuation**
```
┌──────────────┬──────────┬──────────────┬───────────┬─────────────┐
│ Metric       │ Value    │ vs Sector    │ vs 5Y Avg │ Signal      │
├──────────────┼──────────┼──────────────┼───────────┼─────────────┤
│ P/E (TTM)    │ [XX]x    │ [+/-X%]      │ [+/-X%]   │ [ch/fair/ex]│
│ P/E (Fwd)    │ [XX]x    │ [+/-X%]      │ —         │             │
│ P/B          │ [X.X]x   │ [+/-X%]      │ [+/-X%]   │             │
│ EV/EBITDA    │ [XX]x    │ [+/-X%]      │ [+/-X%]   │             │
│ Div Yield    │ [X.X]%   │ IDX avg [X]% │ [+/-X%]   │             │
│ PEG          │ [X.X]x   │ —            │ —         │             │
└──────────────┴──────────┴──────────────┴───────────┴─────────────┘
```

**3B. Growth (YoY)**
```
┌─────────────────┬────────────┬──────────┬─────────────┬──────────┐
│ Metric          │ Latest Qtr │ TTM      │ 3Y CAGR     │ Signal   │
├─────────────────┼────────────┼──────────┼─────────────┼──────────┤
│ Revenue         │ [+/-X%]    │ [+/-X%]  │ [+/-X%]     │          │
│ Gross Profit    │ [+/-X%]    │ [+/-X%]  │ [+/-X%]     │          │
│ EBITDA          │ [+/-X%]    │ [+/-X%]  │ [+/-X%]     │          │
│ Net Profit      │ [+/-X%]    │ [+/-X%]  │ [+/-X%]     │          │
│ EPS             │ [+/-X%]    │ [+/-X%]  │ [+/-X%]     │          │
└─────────────────┴────────────┴──────────┴─────────────┴──────────┘
```

**3C. Quality & Health**
```
┌──────────────────┬─────────┬──────────────────┬────────┐
│ Metric           │ Value   │ Benchmark        │ Flag   │
├──────────────────┼─────────┼──────────────────┼────────┤
│ ROE              │ [X.X]%  │ >15% = good      │ ✓ / ⚠️ │
│ ROA              │ [X.X]%  │ >8% = good       │ ✓ / ⚠️ │
│ Net Margin       │ [X.X]%  │ sector-specific  │        │
│ Gross Margin     │ [X.X]%  │ sector-specific  │        │
│ D/E Ratio        │ [X.X]x  │ <1.0 = good      │ ✓ / ⚠️ │
│ Net Debt/EBITDA  │ [X.X]x  │ <3x = good       │ ✓ / ⚠️ │
│ Current Ratio    │ [X.X]x  │ >1.5 = good      │ ✓ / ⚠️ │
│ Interest Cov     │ [X.X]x  │ >5x = good       │ ✓ / ⚠️ │
│ FCF Yield        │ [X.X]%  │ >5% = attractive │        │
└──────────────────┴─────────┴──────────────────┴────────┘
```

---

## TEMPLATE 4 — Broker Flow Summary
*(From get_broker_summary)*

```
┌─────────────────────────────────────────────────────────────────┐
│  BROKER FLOW SUMMARY  —  [TICKER]  |  [Period]                  │
├────────────────────────────────────────────────────────────────┤
│  TOP BUYERS                                                      │
├───────┬───────────────────┬────────────┬──────────┬────────────┤
│ Code  │ Broker Name       │ Net Buy (B)│ Type     │ Signal     │
├───────┼───────────────────┼────────────┼──────────┼────────────┤
│ [XX]  │ [Name]            │ +IDR [X]B  │ [F.Inst] │ Accum      │
│ [XX]  │ [Name]            │ +IDR [X]B  │ [D.Inst] │ Accum      │
│ [XX]  │ [Name]            │ +IDR [X]B  │ [Retail] │ —          │
├────────────────────────────────────────────────────────────────┤
│  TOP SELLERS                                                     │
├───────┬───────────────────┬────────────┬──────────┬────────────┤
│ [XX]  │ [Name]            │ -IDR [X]B  │ [F.Inst] │ Distrib    │
│ [XX]  │ [Name]            │ -IDR [X]B  │ [D.Inst] │ Distrib    │
├────────────────────────────────────────────────────────────────┤
│  Net Foreign:  [+/-IDR X]B  |  Interpretation: [Accum/Neutral/Distrib]│
└─────────────────────────────────────────────────────────────────┘
```

---

## TEMPLATE 5 — Risk Matrix
*(Step 5 output)*

```
┌──────────────────┬────────────┬─────────────┬──────────────────────────────┐
│ Risk Type        │ Impact     │ Probability │ Mitigation                   │
├──────────────────┼────────────┼─────────────┼──────────────────────────────┤
│ Market (IHSG)    │ [H/M/L]    │ [H/M/L]     │ [e.g., Reduce on IHSG -2%]  │
│ Company-specific │ [H/M/L]    │ [H/M/L]     │ [e.g., Earnings miss watch] │
│ Industry         │ [H/M/L]    │ [H/M/L]     │ [e.g., Commodity price drop]│
│ Liquidity        │ [H/M/L]    │ [H/M/L]     │ [e.g., Limit orders only]   │
│ Currency (IDR)   │ [H/M/L]    │ [H/M/L]     │ [e.g., Hedge if USD debt]   │
│ Regulatory       │ [H/M/L]    │ [H/M/L]     │ [e.g., Monitor OJK policy]  │
│ Execution        │ [H/M/L]    │ [H/M/L]     │ [e.g., Scale in 3 tranches] │
└──────────────────┴────────────┴─────────────┴──────────────────────────────┘
```

---

## TEMPLATE 6 — Catalyst Calendar
*(Step 5 output)*

```
┌─────────────────┬───────────────────────────────┬─────────────────────┐
│ Date / Period   │ Event                         │ Expected Impact     │
├─────────────────┼───────────────────────────────┼─────────────────────┤
│ [DD-MMM-YYYY]   │ [Quarterly earnings release]  │ [+/- if beat/miss]  │
│ [DD-MMM-YYYY]   │ [Dividend ex-date]            │ [Yield: X%]         │
│ [Q1/Q2/YYYY]    │ [AGM / RUPST]                 │ [Dividend approval] │
│ [Month YYYY]    │ [Commodity price review]      │ [Sector catalyst]   │
│ [Ongoing]       │ [BI Rate decision]            │ [Rate sensitive]    │
└─────────────────┴───────────────────────────────┴─────────────────────┘
```

---

## TEMPLATE 7 — Trade Parameters
*(Step 6 output)*

```
┌────────────────────────────────────────────────────────────────────┐
│  TRADE PARAMETERS  —  [TICKER]  |  Recommendation: [BUY/SELL/HOLD] │
├────────────────┬────────────────────────────────────────────────── ┤
│ Entry Zone     │ IDR [X,XXX] – [X,XXX]   ([method: near EMA20 / support])│
│ Stop Loss      │ IDR [X,XXX]             (-[X]% from entry midpoint)     │
│ Take Profit 1  │ IDR [X,XXX]             (+[X]% — resistance 1)          │
│ Take Profit 2  │ IDR [X,XXX]             (+[X]% — resistance 2)          │
│ Take Profit 3  │ IDR [X,XXX]             (+[X]% — extended target)       │
│ R:R Ratio      │ [X.X]:1                                                 │
│ Max Position   │ [X]% of portfolio                                       │
│ Hold Period    │ [X days / X weeks / X months]                           │
│ Entry Strategy │ [Single entry / Scale in 3 tranches]                    │
├────────────────┼─────────────────────────────────────────────────────────┤
│ ARA Limit      │ IDR [X,XXX]   (+35% ceiling)                            │
│ ARB Limit      │ IDR [X,XXX]   (-35% floor)                              │
└────────────────┴─────────────────────────────────────────────────────────┘
Profit-taking: Sell 50% at TP1, 30% at TP2, 20% at TP3 (or hold for TP3 if thesis intact)
Stop adjustment: Move stop to breakeven once TP1 hit.
Exit triggers: [e.g., Close below SMA50 on high volume / Earnings miss >15% / D/E >2x]
```

---

## TEMPLATE 8 — Scenario Analysis
*(Step 6 output)*

```
┌──────────┬──────┬──────────────┬───────────┬───────────────────────────┐
│ Scenario │ Prob │ Target Price │ Return    │ Key Assumption             │
├──────────┼──────┼──────────────┼───────────┼───────────────────────────┤
│ Bull     │  25% │ IDR [X,XXX]  │ +[X]%     │ [e.g., Earnings beat 20%] │
│ Base     │  50% │ IDR [X,XXX]  │ +[X]%     │ [e.g., Consensus earnings]│
│ Bear     │  25% │ IDR [X,XXX]  │ -[X]%     │ [e.g., Commodity drop 15%]│
├──────────┼──────┼──────────────┼───────────┼───────────────────────────┤
│ Prob-Wtd │ 100% │ —            │ +[X]%     │ Expected return            │
└──────────┴──────┴──────────────┴───────────┴───────────────────────────┘
Time horizon: [X months]
```

---

## TEMPLATE 9 — Sources & Disclaimer
*(Required on every report)*

```
─────────────────────────────────────────────────────────
SOURCES
IDX-MCP:   get_stock_price | get_financials | get_technicals | get_broker_summary
           get_foreign_flow | get_stock_news | get_company_profile | get_market_overview
           [MA Ketat] scan_today | get_top10 | analyze_ticker | get_prediction | run_backtest | get_scan_summary
           [GoldenX] scan_golden_cross | get_top_golden_cross | analyze_golden_cross
           Retrieved: [HH:MM WIB, DD-MMM-YYYY]
Financials: [Reporting period, e.g., FY2024 annual / Q3-2024 quarterly]
Web:        [List any supplementary web sources used]
─────────────────────────────────────────────────────────
DISCLAIMER
For informational purposes only. Not investment advice.
Verify all data independently before trading. Capital at risk.
AI-generated analysis may contain errors, omissions, or delayed data.
Past performance does not predict future returns.
─────────────────────────────────────────────────────────
```

---

## TEMPLATE 10 — Quick Price Check (minimal output)
*(For single-line price requests)*

```
[TICKER]  IDR [X,XXX]  [+/-X.X%]  |  Vol: [X]M shrs  |  Value: IDR [X]B
IHSG: [X,XXX] ([+/-X.X%])  |  [Session status]  |  [DD-MMM-YYYY HH:MM WIB]
Context: [One sentence — e.g., "Trading near 52W high; above all MAs; RSI 62 (neutral)"]
```

---

## TEMPLATE 11 — Sector Comparative Scorecard
*(For "which stock in X sector" queries)*

```
┌──────────┬────────┬──────┬───────┬──────────┬──────┬───────┬────────────┐
│ Ticker   │ Price  │ P/E  │ ROE   │ Rev Growth│ Score│ Flow  │ Rating     │
├──────────┼────────┼──────┼───────┼──────────┼──────┼───────┼────────────┤
│ [TICK1]  │ [X,XXX]│ [X]x │ [X]%  │ [+X]%    │ [X.X]│ [Acc] │ BUY        │
│ [TICK2]  │ [X,XXX]│ [X]x │ [X]%  │ [+X]%    │ [X.X]│ [Neu] │ HOLD       │
│ [TICK3]  │ [X,XXX]│ [X]x │ [X]%  │ [+X]%    │ [X.X]│ [Dist]│ SELL       │
└──────────┴────────┴──────┴───────┴──────────┴──────┴───────┴────────────┘
Best pick: [TICKER] — [one-line reason]
```

---

## TEMPLATE 12 — MA Ketat Market Scan Results
*(Output from scan_today / get_top10 — use for screening requests)*

```
┌─────────────────────────────────────────────────────────────────────────┐
│  MA KETAT SCAN  —  [DD-MMM-YYYY]  |  Scanned: [X] stocks               │
│  Signals found: [X]  |  tick_threshold: [X]  |  vol_threshold: [X]%    │
├───────┬─────────┬────────────┬──────────┬─────────┬────────────────────┤
│ Rank  │ Ticker  │ Price      │ MA Spread│ Vol 10D │ Confidence / Signal │
├───────┼─────────┼────────────┼──────────┼─────────┼────────────────────┤
│  1    │ [TICK]  │ IDR [X,XXX]│ [X.X]t   │ [X.X]%  │ [score] — [signal] │
│  2    │ [TICK]  │ IDR [X,XXX]│ [X.X]t   │ [X.X]%  │ [score] — [signal] │
│  3    │ [TICK]  │ IDR [X,XXX]│ [X.X]t   │ [X.X]%  │ [score] — [signal] │
│  4    │ [TICK]  │ IDR [X,XXX]│ [X.X]t   │ [X.X]%  │ [score] — [signal] │
│  5    │ [TICK]  │ IDR [X,XXX]│ [X.X]t   │ [X.X]%  │ [score] — [signal] │
│  6    │ [TICK]  │ IDR [X,XXX]│ [X.X]t   │ [X.X]%  │ [score] — [signal] │
│  7    │ [TICK]  │ IDR [X,XXX]│ [X.X]t   │ [X.X]%  │ [score] — [signal] │
│  8    │ [TICK]  │ IDR [X,XXX]│ [X.X]t   │ [X.X]%  │ [score] — [signal] │
│  9    │ [TICK]  │ IDR [X,XXX]│ [X.X]t   │ [X.X]%  │ [score] — [signal] │
│ 10    │ [TICK]  │ IDR [X,XXX]│ [X.X]t   │ [X.X]%  │ [score] — [signal] │
└───────┴─────────┴────────────┴──────────┴─────────┴────────────────────┘
Sector breakdown: [Sector A: X stocks] | [Sector B: X stocks] | ...
Market context:   IHSG [X,XXX] ([+/-X.X%]) — [Risk-on / Risk-off / Neutral]
Next step: Run analyze_ticker on top 3 for entry confirmation.
```

---

## TEMPLATE 13 — MA Ketat Single Ticker Analysis
*(Output from analyze_ticker — use for deep MA Ketat assessment of one stock)*

```
┌─────────────────────────────────────────────────────────────────────────┐
│  MA KETAT ANALYSIS  —  [TICKER]  |  [DD-MMM-YYYY]  |  Period: [Xmo]    │
├──────────────────────────────────────────────────────────────────────┤
│  MOVING AVERAGE STACK                                                   │
├──────────────┬────────────────┬────────────────────────────────────────┤
│ MA           │ Value          │ vs Price                               │
├──────────────┼────────────────┼────────────────────────────────────────┤
│ SMA 3        │ IDR [X,XXX]    │ [+/-X.X%]                             │
│ SMA 5        │ IDR [X,XXX]    │ [+/-X.X%]                             │
│ SMA 10       │ IDR [X,XXX]    │ [+/-X.X%]                             │
│ SMA 20       │ IDR [X,XXX]    │ [+/-X.X%]                             │
│ SMA 50       │ IDR [X,XXX]    │ [+/-X.X%]                             │
├──────────────┼────────────────┼────────────────────────────────────────┤
│ MA Spread    │ [X.X] ticks    │ [Tight ✓ / Moderate / Too wide ✗]    │
│ Volatility   │ [X.X]% (10D)   │ [Low ✓ / High ✗]                     │
├──────────────┴────────────────┴────────────────────────────────────────┤
│  ENTRY FILTER RESULT                                                    │
├────────────────────────────────────────────────────────────────────────┤
│ Passes Entry:   [YES ✓ / NO ✗]                                         │
│ Signal Active:  [YES ✓ / NO ✗]                                         │
│ Result:         [ACTIVE SETUP / NEAR SETUP / NO SETUP]                 │
│ Note:           [fail_reason or "All filters passed"]                   │
└────────────────────────────────────────────────────────────────────────┘
[If signal_present = true → append TEMPLATE 14 prediction block below]
```

---

## TEMPLATE 14 — MA Ketat Short-Term Prediction
*(Output from get_prediction — append to Template 13 when signal active, or standalone)*

```
┌─────────────────────────────────────────────────────────────────────────┐
│  MA KETAT PREDICTION  —  [TICKER]  |  Horizon: [X] trading days        │
├──────────────┬──────────────────────────────────────────────────────────┤
│ Current Price│ IDR [X,XXX]                                             │
├──────────────┼──────────────────────────────────────────────────────────┤
│ Expected Gain│ [+X.X]% — [+X.X]%  (midpoint: [+X.X]%)               │
│ Target 1     │ IDR [X,XXX]   ([+X.X]% upside)                        │
│ Target 2     │ IDR [X,XXX]   ([+X.X]% upside)  [skip if N/A]        │
│ Stop Loss    │ IDR [X,XXX]   (-[X.X]% downside)                      │
│ R:R Ratio    │ [X.X]:1   [Favorable ✓ if ≥ 2.0 / Marginal if < 1.5] │
├──────────────┼──────────────────────────────────────────────────────────┤
│ Basis        │ [Description of signal driver]                          │
│ Confidence   │ [high / moderate / low]                                 │
└──────────────┴──────────────────────────────────────────────────────────┘
⚠️ Rule-based forecast only. Not a guarantee. Cross-check with technicals.
```

---

## TEMPLATE 15 — MA Ketat Backtest Results
*(Output from run_backtest — use to validate signal reliability)*

```
┌─────────────────────────────────────────────────────────────────────────┐
│  BACKTEST RESULTS  —  [TICKER]  |  Period: [X year(s)]                 │
│  tick_threshold: [X.X]  |  vol_threshold: [X.X]%                       │
├──────────────────────┬──────────────────────────────────────────────────┤
│ Signals found        │ [X]   [sample quality: Small/Adequate/Large]    │
│ Win Rate (7D)        │ [X]%  [Reliable >65% / Moderate 50-65% / Weak] │
│ Avg Return (7D)      │ [+/-X.X]%                                       │
│ Best Trade           │ [+X.X]%  ([DD-MMM-YYYY])                       │
│ Worst Trade          │ [-X.X]%  ([DD-MMM-YYYY])                       │
│ Max Drawdown         │ [-X.X]%                                         │
├──────────────────────┴──────────────────────────────────────────────────┤
│  SIGNAL HISTORY (last 5)                                                │
├────────────────┬─────────────────┬──────────────────────────────────────┤
│ Date           │ Entry Price     │ 7D Return                           │
├────────────────┼─────────────────┼──────────────────────────────────────┤
│ [DD-MMM-YYYY]  │ IDR [X,XXX]     │ [+/-X.X]%                          │
│ [DD-MMM-YYYY]  │ IDR [X,XXX]     │ [+/-X.X]%                          │
│ [DD-MMM-YYYY]  │ IDR [X,XXX]     │ [+/-X.X]%                          │
│ [DD-MMM-YYYY]  │ IDR [X,XXX]     │ [+/-X.X]%                          │
│ [DD-MMM-YYYY]  │ IDR [X,XXX]     │ [+/-X.X]%                          │
└────────────────┴─────────────────┴──────────────────────────────────────┘
Verdict: [Signal RELIABLE / MODERATE / UNRELIABLE on this ticker]
Action:  [Proceed / Use tight stops / Avoid MA Ketat strategy for this stock]
```

---

## TEMPLATE 16 — Golden Cross Scan Results
*(Output from scan_golden_cross / get_top_golden_cross — use for dip-buy screening requests)*

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  GOLDEN CROSS SCAN  —  [DD-MMM-YYYY]  |  Scanned: [X] stocks                │
│  Signals found: [X]  |  stoch_threshold: [X]  |  min_volume: [X]K          │
├────┬───────┬─────────┬────────┬────────┬────────┬──────────┬────────────────┤
│Rank│Ticker │  Price  │ SMA50  │ SMA200 │Stoch %K│  RSI   │ Score / Fresh  │
├────┼───────┼─────────┼────────┼────────┼────────┼──────────┼────────────────┤
│  1 │[TICK] │IDR[X,X] │[X,XXX] │[X,XXX] │ [XX.X] │ [XX.X] │[XX] [✓Fresh/—] │
│  2 │[TICK] │IDR[X,X] │[X,XXX] │[X,XXX] │ [XX.X] │ [XX.X] │[XX] [✓Fresh/—] │
│  3 │[TICK] │IDR[X,X] │[X,XXX] │[X,XXX] │ [XX.X] │ [XX.X] │[XX] [✓Fresh/—] │
│  4 │[TICK] │IDR[X,X] │[X,XXX] │[X,XXX] │ [XX.X] │ [XX.X] │[XX] [✓Fresh/—] │
│  5 │[TICK] │IDR[X,X] │[X,XXX] │[X,XXX] │ [XX.X] │ [XX.X] │[XX] [✓Fresh/—] │
│  6 │[TICK] │IDR[X,X] │[X,XXX] │[X,XXX] │ [XX.X] │ [XX.X] │[XX] [✓Fresh/—] │
│  7 │[TICK] │IDR[X,X] │[X,XXX] │[X,XXX] │ [XX.X] │ [XX.X] │[XX] [✓Fresh/—] │
│  8 │[TICK] │IDR[X,X] │[X,XXX] │[X,XXX] │ [XX.X] │ [XX.X] │[XX] [✓Fresh/—] │
│  9 │[TICK] │IDR[X,X] │[X,XXX] │[X,XXX] │ [XX.X] │ [XX.X] │[XX] [✓Fresh/—] │
│ 10 │[TICK] │IDR[X,X] │[X,XXX] │[X,XXX] │ [XX.X] │ [XX.X] │[XX] [✓Fresh/—] │
└────┴───────┴─────────┴────────┴────────┴────────┴──────────┴────────────────┘
Fresh = golden cross within last 10 sessions  |  Score = 0–100 confidence
Market context: IHSG [X,XXX] ([+/-X.X%]) — [Risk-on / Risk-off / Neutral]
Next step: Run analyze_golden_cross on top 3 for full assessment + entry parameters.
```

---

## TEMPLATE 17 — Golden Cross Single Ticker Analysis
*(Output from analyze_golden_cross — use for deep golden cross assessment of one stock)*

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  GOLDEN CROSS ANALYSIS  —  [TICKER]  |  [DD-MMM-YYYY]  |  Period: [Xy]     │
├──────────────────────────────────────────────────────────────────────────────┤
│  GOLDEN CROSS STATUS                                                          │
├─────────────────────┬────────────────────┬──────────────────────────────────┤
│ SMA50               │ IDR [X,XXX]        │ [X.X% above/below SMA200]        │
│ SMA200              │ IDR [X,XXX]        │ [Stop anchor]                    │
│ Cross Confirmed     │ [YES ✓ / NO ✗]     │ SMA50 [> / ≤] SMA200             │
│ Days Since Cross    │ [X] sessions       │ [✓ Fresh (<10) / — Established]  │
├─────────────────────┴────────────────────┴──────────────────────────────────┤
│  STOCHASTIC SLOW (14,3,3)                                                     │
├─────────────────────┬────────────────────┬──────────────────────────────────┤
│ %K (Slow)           │ [XX.X]             │ [Oversold ✓ <25 / Not Oversold]  │
│ %D (Signal line)    │ [XX.X]             │ —                                │
│ Momentum            │ [Bullish ✓ / ✗]    │ [K≥D or fresh K>D cross]         │
├─────────────────────┴────────────────────┴──────────────────────────────────┤
│  OTHER INDICATORS                                                             │
├─────────────────────┬────────────────────┬──────────────────────────────────┤
│ RSI (14)            │ [XX.X]             │ [< 50 ✓ pullback / ≥ 50 ✗]      │
│ Volume today        │ [X.XM shrs]        │ [vs 20D avg: X.XM]               │
│ Dist from SMA200    │ [+X.X]%            │ [≤5% ideal / ≤10% good / >10%]  │
├─────────────────────┴────────────────────┴──────────────────────────────────┤
│  ENTRY FILTER RESULT                                                          │
├─────────────────────────────────────────────────────────────────────────────┤
│ Passes Filters:  [YES ✓ / NO ✗]                                              │
│ Assessment:      [PASS — Golden cross dip-buy signal detected.]              │
│                  [FAIL — reason1; reason2; ...]                              │
├─────────────────────────────────────────────────────────────────────────────┤
│  ENTRY PARAMETERS  (only if passes_filters = true)                            │
├─────────────────────┬────────────────────────────────────────────────────────┤
│ Entry Zone          │ IDR [X,XXX] – [X,XXX]   (±2 ticks around close)       │
│ Stop Loss           │ IDR [X,XXX]              (SMA200 - 1 tick = [-X.X%])  │
│ Target 1            │ IDR [X,XXX]              ([+X.X]% upside)             │
│ Target 2            │ IDR [X,XXX]              ([+X.X]% upside)             │
│ R:R Ratio           │ [X.X]:1                  [Favorable ✓ if ≥ 2.0]      │
│ Signal Strength     │ [STRONG / MEDIUM / WEAK]                               │
│ Confidence Score    │ [XX]/100                 [H≥70 / M≥50 / L<50]        │
├─────────────────────┴────────────────────────────────────────────────────────┤
│  PREDICTION BASIS                                                             │
│  [Rationale text from prediction.rationale field]                             │
└─────────────────────────────────────────────────────────────────────────────┘
⚠️ Stop anchored at SMA200 — exit if price closes below SMA200 on high volume.
⚠️ Rule-based forecast only. Not a guarantee. Cross-check with technicals.
```
