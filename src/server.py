"""MCP server entry point — registers all IDX stock data tools."""

import asyncio
import json
import logging
import math
import os
import sys
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

try:
    from .tools.price import get_stock_price
    from .tools.financials import get_financials
    from .tools.technicals import get_technicals
    from .tools.broker_summary import get_broker_summary
    from .tools.foreign_flow import get_foreign_flow
    from .tools.predictions import (
        gather_intelligence,
        evaluate_and_log_thesis
    )
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
except ImportError as e:
    import logging
    logging.error(f"Failed to import tools: {e}")
    # Define dummy functions so the server doesn't crash on boot
    async def _dummy(*args, **kwargs):
        raise RuntimeError("Tools failed to load due to missing dependencies.")
    get_stock_price = get_financials = get_technicals = get_broker_summary = get_foreign_flow = _dummy
    gather_intelligence = evaluate_and_log_thesis = get_market_overview = get_company_profile = _dummy
    scan_ma_breakout = get_top10 = analyze_ticker = get_prediction = run_backtest = get_scan_summary = _dummy
    scan_golden_cross = get_top_golden_cross = analyze_golden_cross = _dummy
    scan_mean_reversion = scan_volatility_squeeze = scan_volume_accumulation = _dummy

# Set up logging
LOG_DIR = Path.home() / ".idx-mcp" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "error.log"

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


# Create MCP server
server = Server("idx-mcp")


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
        description="Calculates Expected Value (EV) and logs the trade thesis for forward testing. Use this after determining win_prob from gather_intelligence.",
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
                    "description": "Absolute IDR profit amount if the target is reached",
                },
                "loss_target_idr": {
                    "type": "number",
                    "description": "Absolute IDR loss amount if the stop-loss is reached",
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
            "required": ["ticker", "win_prob", "profit_target_idr", "loss_target_idr", "reasoning", "target_date", "strategy_name"],
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
        description="Run full BEI Mean Reversion scan (deep oversold). Finds stocks with RSI < 30 and price deeply below SMA20.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="scan_volatility_squeeze",
        description="Run full BEI Volatility Squeeze scan. Finds stocks where Bollinger Bands are at 6-month lows combined with rising MACD.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="scan_volume_accumulation",
        description="Run full BEI Volume Accumulation scan. Finds stocks trading at 300%+ of 20-day average volume with tight price spread.",
        inputSchema={"type": "object", "properties": {}},
    ),
]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Route tool calls to the appropriate handler."""
    try:
        if name == "get_stock_price":
            result = await get_stock_price(arguments["ticker"])
        elif name == "get_financials":
            result = await get_financials(
                arguments["ticker"],
                arguments.get("period", "annual"),
            )
        elif name == "get_technicals":
            result = await get_technicals(
                arguments["ticker"],
                arguments.get("period", "3mo"),
            )
        elif name == "get_broker_summary":
            result = await get_broker_summary(arguments["ticker"])
        elif name == "get_foreign_flow":
            result = await get_foreign_flow(
                arguments.get("ticker"),
                arguments.get("period", "daily"),
            )
        elif name == "gather_intelligence":
            result = await gather_intelligence(
                arguments["ticker"],
                arguments.get("lookback_days", 60),
                arguments.get("max_articles", 5),
            )
        elif name == "evaluate_and_log_thesis":
            result = await evaluate_and_log_thesis(
                arguments["ticker"],
                arguments["win_prob"],
                arguments["profit_target_idr"],
                arguments["loss_target_idr"],
                arguments["reasoning"],
                arguments["target_date"],
                arguments["strategy_name"],
                arguments.get("buy_fee_rate", 0.0015),
                arguments.get("sell_fee_rate", 0.0025),
            )
        elif name == "get_market_overview":
            result = await get_market_overview(
                arguments.get("include_macro", True),
            )
        elif name == "get_company_profile":
            result = await get_company_profile(arguments["ticker"])
        elif name == "scan_ma_breakout":
            result = await scan_ma_breakout(
                arguments.get("tick_threshold", 6.0),
                arguments.get("vol_threshold", 3.8),
            )
        elif name == "get_top10":
            result = await get_top10()
        elif name == "analyze_ticker":
            result = await analyze_ticker(
                arguments["ticker"],
                arguments.get("period", "6mo"),
            )
        elif name == "get_prediction":
            result = await get_prediction(
                arguments["ticker"],
                arguments.get("horizon_days", 7),
            )
        elif name == "run_backtest":
            result = await run_backtest(
                arguments["ticker"],
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
            result = await analyze_golden_cross(arguments["ticker"], arguments.get("period", "1y"))
        elif name == "scan_mean_reversion":
            result = await scan_mean_reversion()
        elif name == "scan_volatility_squeeze":
            result = await scan_volatility_squeeze()
        elif name == "scan_volume_accumulation":
            result = await scan_volume_accumulation()
        else:
            result = {
                "error": True,
                "error_type": "unknown_tool",
                "message": f"Unknown tool: {name}",
                "partial_data": None,
                "suggestion": (
                    "Available tools: get_stock_price, get_financials, get_technicals, "
                    "get_broker_summary, get_foreign_flow, gather_intelligence, "
                    "evaluate_and_log_thesis, get_market_overview, "
                    "get_company_profile, scan_ma_breakout, get_top10, analyze_ticker, "
                    "get_prediction, run_backtest, get_scan_summary, "
                    "scan_golden_cross, get_top_golden_cross, analyze_golden_cross, "
                    "scan_mean_reversion, scan_volatility_squeeze, scan_volume_accumulation"
                ),
            }

        return [TextContent(type="text", text=json.dumps(_sanitize_nans(result), ensure_ascii=False, indent=2, default=str))]

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
    logger.info("Starting idx-mcp server...")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main():
    """Entry point for the idx-mcp server."""
    asyncio.run(run())


if __name__ == "__main__":
    main()
