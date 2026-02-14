"""MCP server entry point — registers all IDX stock data tools."""

import asyncio
import json
import logging
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
        else:
            result = {
                "error": True,
                "error_type": "unknown_tool",
                "message": f"Unknown tool: {name}",
                "partial_data": None,
                "suggestion": "Available tools: get_stock_price, get_financials, get_technicals, get_broker_summary, get_foreign_flow, get_stock_news, get_market_overview, get_company_profile",
            }

        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2, default=str))]

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
