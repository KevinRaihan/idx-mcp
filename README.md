# idx-mcp — IDX Stock Data MCP Server

A local MCP (Model Context Protocol) server that provides Indonesian Stock Exchange (IDX) data to Claude Desktop. Exposes 8 tools returning structured JSON for stock prices, financials, technicals, broker flow, news, and more.

## Architecture

```
Claude Desktop / Claude Code
        ↓ MCP protocol (stdio transport)
   idx-mcp server (runs locally)
        ↓
   Data Sources:
   ├── yfinance (price, fundamentals, historicals)
   ├── Web scraper (Stockbit broker summary, foreign flow)
   ├── Web scraper (IDX official — company profiles)
   ├── News scraper (Kontan, Bisnis, CNBC Indonesia)
   └── Computed (technical indicators via pandas-ta)
```

## Tools

| Tool | Description |
|------|-------------|
| `get_stock_price` | Current/last price and trading data |
| `get_financials` | Income statement, balance sheet, valuation ratios |
| `get_technicals` | RSI, MACD, moving averages, support/resistance |
| `get_broker_summary` | Top buying/selling brokers (from Stockbit) |
| `get_foreign_flow` | Net foreign buy/sell flow |
| `get_stock_news` | Recent news from Indonesian financial media |
| `get_market_overview` | IHSG level, sectors, macro snapshot |
| `get_company_profile` | Company info, ownership, index membership |

## Installation

### Prerequisites
- Python 3.11+

### Install dependencies

```bash
cd idx-mcp
pip install -e .
```

Or using requirements.txt:

```bash
pip install -r requirements.txt
```

## Claude Desktop Configuration

Add the following to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "idx-mcp": {
      "command": "python",
      "args": ["-m", "src.server"],
      "cwd": "D:\\StockMCP\\idx-mcp"
    }
  }
}
```

Restart Claude Desktop after updating the config. Claude should see 8 new tools.

## Usage

After configuration, test with prompts like:
- "Get me the current price of BBCA"
- "Show me TLKM financials"
- "What are the technicals for ASII?"
- "Who are the top brokers buying BBRI today?"
- "Show me the market overview"

## Data Sources

- **yfinance** — Price, fundamentals, historical OHLCV
- **Stockbit** — Broker summary, foreign flow (web scraping)
- **IDX** — Company profiles (web scraping)
- **Kontan, Bisnis, CNBC Indonesia** — News (web scraping)
- **pandas-ta** — Technical indicator calculations

## Caching

Responses are cached in-memory with TTL:
- Price/flow data: 5 minutes
- Fundamentals/profile: 1 hour
- News: 15 minutes

## Error Handling

All tools return structured error responses on failure — the server never crashes. Errors are logged to `~/.idx-mcp/logs/error.log`.

## Project Structure

```
idx-mcp/
├── pyproject.toml
├── requirements.txt
├── README.md
├── src/
│   ├── server.py              # MCP server entry point
│   ├── tools/                 # 8 tool implementations
│   ├── scrapers/              # Web scraping modules
│   ├── utils/                 # Cache, formatting, ticker validation
│   └── data/                  # Bundled reference data (JSON)
├── tests/
└── logs/
```
