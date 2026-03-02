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

from .tools.price import get_stock_price
from .tools.financials import get_financials
from .tools.technicals import get_technicals
from .tools.broker_summary import get_broker_summary
from .tools.foreign_flow import get_foreign_flow
from .tools.news import get_stock_news
from .tools.market_overview import get_market_overview
from .tools.company_profile import get_company_profile
from .tools.scanner import (
    scan_today,
    get_top10,
    analyze_ticker,
    get_prediction,
    run_backtest,
    get_scan_summary,
)

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
        name="get_stock_news",
        description="Get recent news articles for an IDX stock from Indonesian financial media (Kontan, Bisnis, CNBC Indonesia).",
        inputSchema={
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "IDX ticker symbol",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max articles to return (default 10, max 20)",
                    "default": 10,
                    "minimum": 1,
                    "maximum": 20,
                },
            },
            "required": ["ticker"],
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
        name="scan_today",
        description=(
            "Run a full BEI MA Ketat (tight moving average) scanner across ~250 actively traded stocks. "
            "Detects stocks where SMA 3/5/10/20/50 lines are tightly compressed (MA Kuncup pattern), "
            "signalling a high-probability breakout setup. Returns ranked signals with confidence scores. "
            "Scan takes ~30–90 seconds due to bulk data download."
        ),
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
        elif name == "get_stock_news":
            result = await get_stock_news(
                arguments["ticker"],
                arguments.get("limit", 10),
            )
        elif name == "get_market_overview":
            result = await get_market_overview(
                arguments.get("include_macro", True),
            )
        elif name == "get_company_profile":
            result = await get_company_profile(arguments["ticker"])
        elif name == "scan_today":
            result = await scan_today(
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
        else:
            result = {
                "error": True,
                "error_type": "unknown_tool",
                "message": f"Unknown tool: {name}",
                "partial_data": None,
                "suggestion": (
                    "Available tools: get_stock_price, get_financials, get_technicals, "
                    "get_broker_summary, get_foreign_flow, get_stock_news, get_market_overview, "
                    "get_company_profile, scan_today, get_top10, analyze_ticker, "
                    "get_prediction, run_backtest, get_scan_summary"
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
