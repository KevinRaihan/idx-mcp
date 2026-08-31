"""MCP server entry point — registers all IDX stock data tools."""

import asyncio
import json
import logging
import math
import sys

from mcp.types import TextContent, Tool

try:
    from .mcp_compat import SDK_GENERATION, build_server, serve_stdio
    from .utils.paths import error_log_file
    from .tools.price import get_stock_price
    from .tools.financials import get_financials
    from .tools.technicals import get_technicals
    from .tools.broker_summary import get_broker_summary
    from .tools.foreign_flow import get_foreign_flow
    from .tools.predictions import (
        gather_intelligence,
        evaluate_and_log_thesis
    )
    from .tools.evaluation import evaluate_predictions
    from .tools.market_overview import get_market_overview
    from .tools.company_profile import get_company_profile
    from .tools.scanner import (
        scan_ma_breakout,
        get_top10,
        analyze_ticker,
        get_prediction,
        run_backtest,
        get_scan_summary,
    )
    from .tools.golden_cross import (
        scan_golden_cross,
        get_top_golden_cross,
        analyze_golden_cross,
    )
    from .tools.mean_reversion import scan_mean_reversion
    from .tools.vol_squeeze import scan_volatility_squeeze
    from .tools.volume_accumulation import scan_volume_accumulation
    from .tools.relative_strength import scan_relative_strength
    from .tools.trend_pullback import scan_trend_pullback
    from .tools.breakout_high import scan_breakout_high
    from .tools.distribution import scan_distribution_warning
    from .tools.gap import scan_gap
except ImportError as e:
    # Failing fast is the only honest option here. Substituting stubs made the
    # server advertise 21 healthy tools that every raised at call time, so a
    # broken install looked like a working one until the first query.
    sys.stderr.write(
        "idx-mcp: failed to import tools — the install is incomplete.\n"
        f"  cause: {e.__class__.__name__}: {e}\n"
        f"  interpreter: {sys.executable}\n"
        "  fix: reinstall dependencies with `uv pip install -e .` "
        "(or `pip install -r requirements.txt`) using this interpreter.\n"
    )
    raise SystemExit(1) from e

# Set up logging. Everything the server writes lives under IDX_MCP_HOME
# (default ~/.idx-mcp) so the package directory stays read-only.
LOG_FILE = error_log_file()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger("idx-mcp")

def _sanitize_nans(obj):
    """Recursively replace float NaN with None so json.dumps produces valid JSON."""
    if isinstance(obj, float) and math.isnan(obj):
        return None
    if isinstance(obj, dict):
        return {k: _sanitize_nans(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_nans(v) for v in obj]
    return obj


__version__ = "1.5.0"


class ToolArgumentError(ValueError):
    """A required tool argument is missing or malformed."""


def _require(arguments: dict, name: str, tool: str):
    """Fetch a required argument, failing with a message the agent can act on."""
    if name not in arguments or arguments[name] is None:
        raise ToolArgumentError(f"{tool} requires the '{name}' argument.")
    return arguments[name]


# --- Tool definitions ---

TOOLS = [
    Tool(
        name="get_stock_price",
        description="Get current/last price and basic trading data for an IDX (Indonesia Stock Exchange) stock. Returns price, change, volume, market cap, 52-week range, and market status.",
        inputSchema={
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": 'IDX ticker symbol (e.g., "BBCA", "TLKM", "ASII")',
                },
            },
            "required": ["ticker"],
        },
    ),
    Tool(
        name="get_financials",
        description="Get key financial metrics and ratios for an IDX stock. Includes income statement, balance sheet, valuation ratios (PE, PB, EV/EBITDA), profitability (ROE, ROA), and cash flow data.",
        inputSchema={
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "IDX ticker symbol",
                },
                "period": {
                    "type": "string",
                    "description": '"annual" (default) or "quarterly"',
                    "enum": ["annual", "quarterly"],
                    "default": "annual",
                },
            },
            "required": ["ticker"],
        },
    ),
    Tool(
        name="get_technicals",
        description="Calculate technical indicators from historical price data for an IDX stock. Includes moving averages (EMA20, SMA50, SMA200), RSI, MACD, Stochastic, volume analysis, support/resistance levels, and trend signals.",
        inputSchema={
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "IDX ticker symbol",
                },
                "period": {
                    "type": "string",
                    "description": 'Lookback period: "3mo" (default), "6mo", or "1y"',
                    "enum": ["3mo", "6mo", "1y"],
                    "default": "3mo",
                },
            },
            "required": ["ticker"],
        },
    ),
    Tool(
        name="get_broker_summary",
        description="Get top buying and selling brokers for an IDX stock. Shows institutional vs retail flow and foreign broker activity. Data scraped from Stockbit.",
        inputSchema={
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "IDX ticker symbol",
                },
            },
            "required": ["ticker"],
        },
    ),
    Tool(
        name="get_foreign_flow",
        description="Get net foreign buy/sell flow for an IDX stock or the overall market. Shows foreign buying/selling pressure and cumulative trends.",
        inputSchema={
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "IDX ticker symbol. If omitted, returns market-wide foreign flow.",
                },
                "period": {
                    "type": "string",
                    "description": '"daily" (default), "weekly", or "monthly"',
                    "enum": ["daily", "weekly", "monthly"],
                    "default": "daily",
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="gather_intelligence",
        description="Unified tool to fetch trade setup (price, SMA, ATR barriers) and recent news headlines in a single call. Use this to gather all context needed to evaluate a stock.",
        inputSchema={
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "IDX ticker symbol (e.g., 'BBCA')",
                },
                "lookback_days": {
                    "type": "integer",
                    "description": "Number of days of OHLCV history to return (default 60)",
                    "default": 60,
                },
                "max_articles": {
                    "type": "integer",
                    "description": "Max news articles to return (default 5)",
                    "default": 5,
                },
            },
            "required": ["ticker"],
        },
    ),
    Tool(
        name="evaluate_and_log_thesis",
        description=(
            "Calculates fee-adjusted Expected Value (EV) and logs the trade thesis for "
            "forward testing. Fees are charged on position_value_idr (transaction value), "
            "not on the profit/loss targets. Returns EV in IDR, the breakeven win "
            "probability, and your edge over it. Use after determining win_prob from "
            "gather_intelligence."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "IDX ticker symbol",
                },
                "win_prob": {
                    "type": "number",
                    "description": "Probability of hitting the profit target (0.0 to 1.0)",
                },
                "profit_target_idr": {
                    "type": "number",
                    "description": "Absolute IDR profit amount if the target is reached (positive number)",
                    "exclusiveMinimum": 0,
                },
                "loss_target_idr": {
                    "type": "number",
                    "description": "Absolute IDR loss amount if the stop-loss is reached (positive number)",
                    "exclusiveMinimum": 0,
                },
                "position_value_idr": {
                    "type": "number",
                    "description": (
                        "Total IDR transaction value of the intended position "
                        "(entry price x shares). IDX brokerage fees are charged on this, "
                        "not on the profit/loss amounts."
                    ),
                    "exclusiveMinimum": 0,
                },
                "reasoning": {
                    "type": "string",
                    "description": "Detailed reasoning for the trade thesis and predicted probability",
                },
                "target_date": {
                    "type": "string",
                    "description": "The date by which the prediction is expected to materialize (YYYY-MM-DD)",
                },
                "strategy_name": {
                    "type": "string",
                    "description": "The name of the scan strategy that triggered this trade (e.g. 'scan_golden_cross', 'scan_mean_reversion')",
                },
                "entry_price": {
                    "type": "number",
                    "description": (
                        "Intended entry price per share. Required: without the trade "
                        "levels the thesis can never be scored against price history."
                    ),
                    "exclusiveMinimum": 0,
                },
                "stop_loss": {
                    "type": "number",
                    "description": (
                        "Stop-loss price per share. Must be below entry_price for a "
                        "long, above it for a short."
                    ),
                    "exclusiveMinimum": 0,
                },
                "target_price": {
                    "type": "number",
                    "description": (
                        "Profit-target price per share. Must be above entry_price for "
                        "a long, below it for a short."
                    ),
                    "exclusiveMinimum": 0,
                },
                "direction": {
                    "type": "string",
                    "description": "Trade direction (default 'long')",
                    "enum": ["long", "short"],
                    "default": "long",
                },
                "buy_fee_rate": {
                    "type": "number",
                    "description": "Buy fee rate (default 0.0015 = 0.15%)",
                    "default": 0.0015,
                },
                "sell_fee_rate": {
                    "type": "number",
                    "description": "Sell fee rate (default 0.0025 = 0.25%)",
                    "default": 0.0025,
                },
            },
            "required": [
                "ticker", "win_prob", "profit_target_idr", "loss_target_idr",
                "position_value_idr", "reasoning", "target_date", "strategy_name",
                "entry_price", "stop_loss", "target_price",
            ],
        },
    ),
    Tool(
        name="evaluate_predictions",
        description=(
            "Score every logged trade thesis against the price history that followed it. "
            "Walks daily bars from the session after each thesis was logged and reports "
            "whether it hit its target or its stop first, plus realized win rate, average "
            "return, realized P&L, and the calibration gap between predicted and realized "
            "win rates — broken down per strategy. This is the forward test: use it to find "
            "out whether a strategy's confidence scores mean anything before trusting them. "
            "A thesis whose target and stop were both touched in one session is scored "
            "pessimistically as a stop and flagged."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "strategy": {
                    "type": "string",
                    "description": (
                        "Only score theses whose strategy name contains this substring "
                        "(e.g. 'golden_cross'). Omit to score everything."
                    ),
                },
                "include_open": {
                    "type": "boolean",
                    "description": (
                        "Include theses that have not yet resolved and are still within "
                        "their target date (default true). Aggregates always count them "
                        "separately from decided trades."
                    ),
                    "default": True,
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="get_market_overview",
        description="Get IDX market overview: IHSG composite index level, sectoral index performance, and macro snapshot (USD/IDR, commodities, BI rate).",
        inputSchema={
            "type": "object",
            "properties": {
                "include_macro": {
                    "type": "boolean",
                    "description": "Include macro data (BI rate, USD/IDR, commodities). Default true.",
                    "default": True,
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="get_company_profile",
        description="Get company profile for an IDX stock. Includes ownership structure, conglomerate group, BUMN status, index membership (LQ45, IDX30, ISSI), and basic company info.",
        inputSchema={
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "IDX ticker symbol",
                },
            },
            "required": ["ticker"],
        },
    ),
    Tool(
        name="scan_ma_breakout",
        description="Run full BEI Moving Average Breakout scan (MA Ketat) for today to find early momentum setups.",
        inputSchema={
            "type": "object",
            "properties": {
                "tick_threshold": {
                    "type": "number",
                    "description": "Max allowed tick-adjusted MA spread (default 6.0; lower = stricter)",
                    "default": 6.0,
                },
                "vol_threshold": {
                    "type": "number",
                    "description": "Max 10-day rolling volatility % (default 3.8; lower = stricter)",
                    "default": 3.8,
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="get_top10",
        description=(
            "Return the Top 10 MA Ketat signals from today's BEI scan, ranked by confidence score. "
            "Uses cached scan results if available; runs fresh scan if not. "
            "Lightweight format optimised for LLM consumption."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ),
    Tool(
        name="analyze_ticker",
        description=(
            "Deep MA Ketat analysis for a single BEI stock. "
            "Shows all MA values, indicator readings, whether the stock passes entry filters, "
            "and explains why if it fails. Includes prediction if signal is present."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "IDX ticker symbol (e.g., 'BBCA')",
                },
                "period": {
                    "type": "string",
                    "description": 'Lookback period: "3mo", "6mo" (default), or "1y"',
                    "enum": ["3mo", "6mo", "1y"],
                    "default": "6mo",
                },
            },
            "required": ["ticker"],
        },
    ),
    Tool(
        name="get_prediction",
        description=(
            "Get a short-term MA Ketat directional forecast for a single BEI stock. "
            "Rule-based prediction (no ML) estimating 3–10 day expected gain range, "
            "target prices, stop-loss, and reward/risk ratio."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "IDX ticker symbol",
                },
                "horizon_days": {
                    "type": "integer",
                    "description": "Forecast horizon in trading days (3–10, default 7)",
                    "default": 7,
                    "minimum": 3,
                    "maximum": 10,
                },
            },
            "required": ["ticker"],
        },
    ),
    Tool(
        name="run_backtest",
        description=(
            "Backtest the MA Ketat signal on a stock's historical data. "
            "Finds all past MA Ketat trigger points and measures 7-day forward returns, "
            "win rate, average return, and max drawdown."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "IDX ticker symbol",
                },
                "period": {
                    "type": "string",
                    "description": 'Historical lookback: "1y" (default) or "2y"',
                    "enum": ["1y", "2y"],
                    "default": "1y",
                },
                "tick_threshold": {
                    "type": "number",
                    "description": "MA range_ticks threshold (default 6.0)",
                    "default": 6.0,
                },
                "vol_threshold": {
                    "type": "number",
                    "description": "Volatility threshold in % (default 3.8)",
                    "default": 3.8,
                },
            },
            "required": ["ticker"],
        },
    ),
    Tool(
        name="get_scan_summary",
        description=(
            "Get a natural-language summary of today's MA Ketat scan results. "
            "Includes top-5 signal bullets, sector context, and overall market assessment. "
            "Designed for direct LLM consumption."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ),
    Tool(
        name="scan_golden_cross",
        description=(
            "Run a full BEI Golden Cross + Stochastic Oversold scan across ~180 actively traded stocks. "
            "Finds stocks in a confirmed SMA50>SMA200 uptrend (golden cross) that have pulled back "
            "to oversold stochastic levels — a 'buy the dip in an uptrend' dip-buy setup. "
            "Returns signals ranked by confidence score (0-100). Scan takes ~30-90 seconds."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "stoch_threshold": {
                    "type": "number",
                    "description": "Stochastic %K oversold threshold (default 25.0; lower = stricter)",
                    "default": 25.0,
                },
                "min_volume": {
                    "type": "integer",
                    "description": "Minimum daily volume filter (default 500,000)",
                    "default": 500000,
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="get_top_golden_cross",
        description=(
            "Return the Top 10 Golden Cross dip-buy signals from today's scan. "
            "Uses cached results if available; runs fresh scan if not. "
            "Lightweight format optimised for LLM consumption."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ),
    Tool(
        name="analyze_golden_cross",
        description=(
            "Deep Golden Cross + Stochastic analysis for a single BEI stock. "
            "Shows SMA50/SMA200 status, days since golden cross, stochastic %K/%D readings, "
            "RSI, volume, whether the stock passes all entry filters, and explains why if it fails. "
            "Includes a rule-based directional prediction if a signal is present."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "IDX ticker symbol (e.g., 'BBCA')",
                },
                "period": {
                    "type": "string",
                    "description": 'Lookback period: "1y" (default) or "2y"',
                    "enum": ["1y", "2y"],
                    "default": "1y",
                },
            },
            "required": ["ticker"],
        },
    ),
    Tool(
        name="scan_mean_reversion",
        description=(
            "Run a full BEI Mean Reversion (deep oversold) scan across ~178 actively traded "
            "stocks. Finds capitulation setups: RSI below the threshold while price sits well "
            "under its SMA20 on real volume. Returns signals ranked by confidence score (0-100). "
            "Scan takes ~30-90 seconds; results cache for 4 hours."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "rsi_threshold": {
                    "type": "number",
                    "description": "RSI(14) oversold threshold (default 30.0; lower = stricter)",
                    "default": 30.0,
                    "minimum": 1,
                    "maximum": 99,
                },
                "min_volume": {
                    "type": "integer",
                    "description": "Minimum daily volume filter (default 500,000)",
                    "default": 500000,
                    "minimum": 0,
                },
                "min_below_sma20_pct": {
                    "type": "number",
                    "description": (
                        "How far below SMA20 the close must sit, in percent "
                        "(default 5.0; higher = stricter)"
                    ),
                    "default": 5.0,
                    "minimum": 0,
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="scan_volatility_squeeze",
        description=(
            "Run a full BEI Volatility Squeeze scan across ~178 actively traded stocks. "
            "Finds stocks whose Bollinger Band width is at its 6-month low while the MACD "
            "histogram is turning up — a coiled setup ahead of an expansion move. "
            "Returns signals ranked by confidence score (0-100). "
            "Scan takes ~30-90 seconds; results cache for 4 hours."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "min_volume": {
                    "type": "integer",
                    "description": "Minimum daily volume filter (default 1,000,000)",
                    "default": 1000000,
                    "minimum": 0,
                },
                "squeeze_tolerance": {
                    "type": "number",
                    "description": (
                        "How close band width must be to its 125-day minimum, as a multiplier "
                        "(default 1.10 = within 10%; lower = stricter, minimum 1.0)"
                    ),
                    "default": 1.10,
                    "minimum": 1.0,
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="scan_volume_accumulation",
        description=(
            "Run a full BEI Volume Accumulation scan across ~178 actively traded stocks. "
            "Finds stocks trading at a multiple of their 20-day average volume while the "
            "intraday range stays tight and the close holds up — quiet accumulation before "
            "a move. Returns signals ranked by confidence score (0-100). "
            "Scan takes ~30-90 seconds; results cache for 4 hours."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "min_volume": {
                    "type": "integer",
                    "description": "Minimum daily volume filter (default 1,000,000)",
                    "default": 1000000,
                    "minimum": 0,
                },
                "vol_multiple": {
                    "type": "number",
                    "description": (
                        "Required multiple of the 20-day average volume "
                        "(default 3.0 = 300%; higher = stricter)"
                    ),
                    "default": 3.0,
                    "exclusiveMinimum": 0,
                },
                "max_spread_pct": {
                    "type": "number",
                    "description": (
                        "Maximum intraday high-low spread as percent of close "
                        "(default 5.0; lower = stricter)"
                    ),
                    "default": 5.0,
                    "exclusiveMinimum": 0,
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="scan_relative_strength",
        description=(
            "Run a full BEI Relative Strength scan across ~178 actively traded stocks. "
            "Ranks stocks by how far they are outperforming the IHSG (^JKSE) over 1, 3 and "
            "6 months, and reports whether the RS line is at a 3-month high. Use this to "
            "check whether a setup found by another scan is actually leading the market or "
            "merely drifting up with it — a strong chart lagging the index is a weak hand. "
            "Scan takes ~30-90 seconds; results cache for 4 hours."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "min_volume": {
                    "type": "integer",
                    "description": "Minimum daily volume filter (default 500,000)",
                    "default": 500000,
                    "minimum": 0,
                },
                "min_excess_3m_pct": {
                    "type": "number",
                    "description": (
                        "Minimum 3-month outperformance over the IHSG, in percentage points "
                        "(default 5.0; higher = stricter)"
                    ),
                    "default": 5.0,
                },
                "require_rs_high": {
                    "type": "boolean",
                    "description": (
                        "Require the RS line to be at or near its 3-month high "
                        "(default false; true = stricter, leadership confirmed)"
                    ),
                    "default": False,
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="scan_trend_pullback",
        description=(
            "Run a full BEI Trend Pullback scan across ~178 actively traded stocks. "
            "Finds dips inside confirmed uptrends: price above SMA200 with SMA50 above "
            "SMA200, pulled back below SMA20 but holding SMA50, RSI cooled into the 40s, "
            "and the 20-day low still above the 60-day low so structure is intact. "
            "Unlike scan_mean_reversion this requires an existing uptrend, and unlike "
            "scan_golden_cross it does not need a recent cross. "
            "Scan takes ~30-90 seconds; results cache for 4 hours."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "min_volume": {
                    "type": "integer",
                    "description": "Minimum daily volume filter (default 500,000)",
                    "default": 500000,
                    "minimum": 0,
                },
                "rsi_min": {
                    "type": "number",
                    "description": "Lower RSI(14) bound for the pullback (default 40.0)",
                    "default": 40.0,
                    "minimum": 0,
                    "maximum": 100,
                },
                "rsi_max": {
                    "type": "number",
                    "description": "Upper RSI(14) bound for the pullback (default 58.0)",
                    "default": 58.0,
                    "minimum": 0,
                    "maximum": 100,
                },
                "max_pullback_pct": {
                    "type": "number",
                    "description": (
                        "Maximum drop from the 60-day high, in percent "
                        "(default 15.0; deeper than this questions the trend)"
                    ),
                    "default": 15.0,
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="scan_breakout_high",
        description=(
            "Run a full BEI Breakout-to-New-High (Darvas box) scan across ~178 actively "
            "traded stocks. Finds stocks closing above the highest high of a tight "
            "multi-month base on confirming volume, and reports whether the breakout is "
            "also a 52-week high. Complements scan_ma_breakout, which requires moving "
            "average compression that a range breakout does not need. "
            "Scan takes ~30-90 seconds; results cache for 4 hours."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "min_volume": {
                    "type": "integer",
                    "description": "Minimum daily volume filter (default 500,000)",
                    "default": 500000,
                    "minimum": 0,
                },
                "lookback_days": {
                    "type": "integer",
                    "description": (
                        "Length of the base the close must break above, in trading days "
                        "(default 60; longer = stricter)"
                    ),
                    "default": 60,
                    "minimum": 10,
                    "maximum": 250,
                },
                "vol_multiple": {
                    "type": "number",
                    "description": (
                        "Required multiple of the 20-day average volume on the breakout bar "
                        "(default 1.5; higher = stricter)"
                    ),
                    "default": 1.5,
                    "exclusiveMinimum": 0,
                },
                "max_base_range_pct": {
                    "type": "number",
                    "description": (
                        "Maximum high-to-low range of the base as percent of its mean price "
                        "(default 25.0; lower = tighter base, stricter)"
                    ),
                    "default": 25.0,
                    "exclusiveMinimum": 0,
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="scan_distribution_warning",
        description=(
            "Run a full BEI Distribution Warning scan across ~178 actively traded stocks. "
            "This is a RISK scan, not a buy list: it flags charts that are breaking down "
            "(close below SMA50, death cross, declining SMA50, MACD negative and falling, "
            "lower highs, heavy-volume down days) and ranks them by severity. Use it to "
            "exit or trim held positions, or to veto a long signal that another scan "
            "produced on the same ticker. A higher score means a worse chart. "
            "Scan takes ~30-90 seconds; results cache for 4 hours."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "min_volume": {
                    "type": "integer",
                    "description": "Minimum daily volume filter (default 500,000)",
                    "default": 500000,
                    "minimum": 0,
                },
                "min_warning_score": {
                    "type": "number",
                    "description": (
                        "Minimum severity score to report, 0-100 "
                        "(default 50.0; higher = only the worst charts)"
                    ),
                    "default": 50.0,
                    "minimum": 0,
                    "maximum": 100,
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="scan_gap",
        description=(
            "Run a full BEI Gap scan across ~178 actively traded stocks. Finds opening gaps "
            "that held (gap up, never filled back to the prior close, closed at or above the "
            "open) or gap-down exhaustion reversals (gapped down, closed above the open). "
            "Each signal reports how much of the IDX auto-rejection (ARA/ARB) band the gap "
            "consumed. Reads the last completed daily bar, so while the market is open this "
            "reflects the previous session. Results cache for 4 hours."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "min_volume": {
                    "type": "integer",
                    "description": "Minimum daily volume filter (default 500,000)",
                    "default": 500000,
                    "minimum": 0,
                },
                "min_gap_pct": {
                    "type": "number",
                    "description": (
                        "Minimum absolute gap from the prior close, in percent "
                        "(default 2.0; higher = stricter)"
                    ),
                    "default": 2.0,
                    "exclusiveMinimum": 0,
                },
                "direction": {
                    "type": "string",
                    "description": (
                        "'up' for gap-ups that held, 'down' for gap-down exhaustion "
                        "reversals, 'both' for either (default 'up')"
                    ),
                    "enum": ["up", "down", "both"],
                    "default": "up",
                },
            },
            "required": [],
        },
    ),
]


async def list_tools() -> list[Tool]:
    return TOOLS


async def call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    """Route tool calls to the appropriate handler."""
    # Clients may omit `arguments` entirely for no-arg tools.
    arguments = arguments or {}
    try:
        if name == "get_stock_price":
            result = await get_stock_price(_require(arguments, "ticker", name))
        elif name == "get_financials":
            result = await get_financials(
                _require(arguments, "ticker", name),
                arguments.get("period", "annual"),
            )
        elif name == "get_technicals":
            result = await get_technicals(
                _require(arguments, "ticker", name),
                arguments.get("period", "3mo"),
            )
        elif name == "get_broker_summary":
            result = await get_broker_summary(_require(arguments, "ticker", name))
        elif name == "get_foreign_flow":
            result = await get_foreign_flow(
                arguments.get("ticker"),
                arguments.get("period", "daily"),
            )
        elif name == "gather_intelligence":
            result = await gather_intelligence(
                _require(arguments, "ticker", name),
                arguments.get("lookback_days", 60),
                arguments.get("max_articles", 5),
            )
        elif name == "evaluate_and_log_thesis":
            # Keyword arguments deliberately: this signature has grown twice, and
            # a positional call silently rebinds every parameter after an insert.
            result = await evaluate_and_log_thesis(
                ticker=_require(arguments, "ticker", name),
                win_prob=_require(arguments, "win_prob", name),
                profit_target_idr=_require(arguments, "profit_target_idr", name),
                loss_target_idr=_require(arguments, "loss_target_idr", name),
                position_value_idr=_require(arguments, "position_value_idr", name),
                reasoning=_require(arguments, "reasoning", name),
                target_date=_require(arguments, "target_date", name),
                strategy_name=_require(arguments, "strategy_name", name),
                entry_price=_require(arguments, "entry_price", name),
                stop_loss=_require(arguments, "stop_loss", name),
                target_price=_require(arguments, "target_price", name),
                direction=arguments.get("direction", "long"),
                buy_fee_rate=arguments.get("buy_fee_rate", 0.0015),
                sell_fee_rate=arguments.get("sell_fee_rate", 0.0025),
            )
        elif name == "evaluate_predictions":
            result = await evaluate_predictions(
                arguments.get("strategy"),
                arguments.get("include_open", True),
            )
        elif name == "get_market_overview":
            result = await get_market_overview(
                arguments.get("include_macro", True),
            )
        elif name == "get_company_profile":
            result = await get_company_profile(_require(arguments, "ticker", name))
        elif name == "scan_ma_breakout":
            result = await scan_ma_breakout(
                arguments.get("tick_threshold", 6.0),
                arguments.get("vol_threshold", 3.8),
            )
        elif name == "get_top10":
            result = await get_top10()
        elif name == "analyze_ticker":
            result = await analyze_ticker(
                _require(arguments, "ticker", name),
                arguments.get("period", "6mo"),
            )
        elif name == "get_prediction":
            result = await get_prediction(
                _require(arguments, "ticker", name),
                arguments.get("horizon_days", 7),
            )
        elif name == "run_backtest":
            result = await run_backtest(
                _require(arguments, "ticker", name),
                arguments.get("period", "1y"),
                arguments.get("tick_threshold", 6.0),
                arguments.get("vol_threshold", 3.8),
            )
        elif name == "get_scan_summary":
            result = await get_scan_summary()
        elif name == "scan_golden_cross":
            result = await scan_golden_cross(
                arguments.get("stoch_threshold", 25.0),
                arguments.get("min_volume", 500_000),
            )
        elif name == "get_top_golden_cross":
            result = await get_top_golden_cross()
        elif name == "analyze_golden_cross":
            result = await analyze_golden_cross(_require(arguments, "ticker", name), arguments.get("period", "1y"))
        elif name == "scan_mean_reversion":
            result = await scan_mean_reversion(
                arguments.get("rsi_threshold", 30.0),
                arguments.get("min_volume", 500_000),
                arguments.get("min_below_sma20_pct", 5.0),
            )
        elif name == "scan_volatility_squeeze":
            result = await scan_volatility_squeeze(
                arguments.get("min_volume", 1_000_000),
                arguments.get("squeeze_tolerance", 1.10),
            )
        elif name == "scan_volume_accumulation":
            result = await scan_volume_accumulation(
                arguments.get("min_volume", 1_000_000),
                arguments.get("vol_multiple", 3.0),
                arguments.get("max_spread_pct", 5.0),
            )
        elif name == "scan_relative_strength":
            result = await scan_relative_strength(
                arguments.get("min_volume", 500_000),
                arguments.get("min_excess_3m_pct", 5.0),
                arguments.get("require_rs_high", False),
            )
        elif name == "scan_trend_pullback":
            result = await scan_trend_pullback(
                arguments.get("min_volume", 500_000),
                arguments.get("rsi_min", 40.0),
                arguments.get("rsi_max", 58.0),
                arguments.get("max_pullback_pct", 15.0),
            )
        elif name == "scan_breakout_high":
            result = await scan_breakout_high(
                arguments.get("min_volume", 500_000),
                arguments.get("lookback_days", 60),
                arguments.get("vol_multiple", 1.5),
                arguments.get("max_base_range_pct", 25.0),
            )
        elif name == "scan_distribution_warning":
            result = await scan_distribution_warning(
                arguments.get("min_volume", 500_000),
                arguments.get("min_warning_score", 50.0),
            )
        elif name == "scan_gap":
            result = await scan_gap(
                arguments.get("min_volume", 500_000),
                arguments.get("min_gap_pct", 2.0),
                arguments.get("direction", "up"),
            )
        else:
            result = {
                "error": True,
                "error_type": "unknown_tool",
                "message": f"Unknown tool: {name}",
                "partial_data": None,
                "suggestion": (
                    "Available tools: get_stock_price, get_financials, get_technicals, "
                    "get_broker_summary, get_foreign_flow, gather_intelligence, "
                    "evaluate_and_log_thesis, evaluate_predictions, get_market_overview, "
                    "get_company_profile, scan_ma_breakout, get_top10, analyze_ticker, "
                    "get_prediction, run_backtest, get_scan_summary, "
                    "scan_golden_cross, get_top_golden_cross, analyze_golden_cross, "
                    "scan_mean_reversion, scan_volatility_squeeze, scan_volume_accumulation, "
                    "scan_relative_strength, scan_trend_pullback, scan_breakout_high, "
                    "scan_distribution_warning, scan_gap"
                ),
            }

        return [TextContent(type="text", text=json.dumps(_sanitize_nans(result), ensure_ascii=False, indent=2, default=str))]

    except ToolArgumentError as e:
        logger.warning("Invalid arguments for tool %s: %s", name, e)
        return [TextContent(type="text", text=json.dumps({
            "error": True,
            "error_type": "invalid_arguments",
            "message": str(e),
            "partial_data": None,
            "suggestion": "Check the tool's inputSchema and resend with the required arguments.",
        }, ensure_ascii=False, indent=2))]

    except Exception as e:
        logger.exception(f"Unhandled error in tool {name}")
        error_result = {
            "error": True,
            "error_type": "internal_error",
            "message": f"Internal server error: {str(e)}",
            "partial_data": None,
            "suggestion": "This is an unexpected error. Please try again.",
        }
        return [TextContent(type="text", text=json.dumps(error_result, ensure_ascii=False, indent=2))]


async def run():
    """Run the MCP server using stdio transport."""
    logger.info(
        "Starting idx-mcp %s (mcp SDK %s, %d tools)",
        __version__, SDK_GENERATION, len(TOOLS),
    )
    server = build_server("idx-mcp", __version__, list_tools, call_tool)
    await serve_stdio(server)


def main():
    """Entry point for the idx-mcp server."""
    asyncio.run(run())


if __name__ == "__main__":
    main()
