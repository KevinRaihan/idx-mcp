"""Stockbit scraping logic for broker summary and foreign flow data."""

import asyncio
import json
import logging
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("idx-mcp.scrapers.stockbit")

HEADERS = {
    "User-Agent": "idx-mcp/1.0 (personal research tool)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
}

# Load broker codes for type classification
_BROKER_DATA_PATH = Path(__file__).parent.parent / "data" / "broker_codes.json"
_broker_cache: dict | None = None


def _load_broker_data() -> dict:
    global _broker_cache
    if _broker_cache is None:
        with open(_BROKER_DATA_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        _broker_cache = data.get("brokers", {})
    return _broker_cache


def _classify_broker(code: str) -> str:
    """Classify a broker code as foreign, domestic_institutional, or retail."""
    brokers = _load_broker_data()
    entry = brokers.get(code.upper())
    if entry:
        return entry.get("type", "unknown")
    return "unknown"


def _get_broker_name(code: str) -> str:
    """Get broker name from code."""
    brokers = _load_broker_data()
    entry = brokers.get(code.upper())
    if entry:
        return entry.get("name", code)
    return code


async def scrape_broker_summary(ticker: str) -> dict:
    """Scrape broker summary data from Stockbit for a given ticker.

    Returns parsed broker summary data or raises an exception on failure.
    """
    url = f"https://stockbit.com/symbol/{ticker}/broker-summary"

    async with httpx.AsyncClient(headers=HEADERS, timeout=15.0, follow_redirects=True) as client:
        await asyncio.sleep(1)  # Politeness delay
        resp = await client.get(url)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")

    top_buyers = []
    top_sellers = []

    # Try to parse broker summary tables
    # Stockbit's structure may change — we attempt common patterns
    tables = soup.find_all("table")

    for table in tables:
        rows = table.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) >= 3:
                code_text = cells[0].get_text(strip=True).upper()
                if not code_text or len(code_text) > 3:
                    continue

                try:
                    # Try to parse net value and lot
                    val_text = cells[1].get_text(strip=True).replace(",", "").replace(".", "")
                    lot_text = cells[2].get_text(strip=True).replace(",", "").replace(".", "")

                    net_value = int(float(val_text)) if val_text else 0
                    net_lot = int(float(lot_text)) if lot_text else 0

                    entry = {
                        "broker_code": code_text,
                        "broker_name": _get_broker_name(code_text),
                        "type": _classify_broker(code_text),
                        "net_value_idr": net_value,
                        "net_lot": net_lot,
                    }

                    if net_value > 0:
                        top_buyers.append(entry)
                    elif net_value < 0:
                        top_sellers.append(entry)
                except (ValueError, IndexError):
                    continue

    # Sort by absolute value
    top_buyers.sort(key=lambda x: x["net_value_idr"], reverse=True)
    top_sellers.sort(key=lambda x: x["net_value_idr"])

    # Limit to top 5
    top_buyers = top_buyers[:5]
    top_sellers = top_sellers[:5]

    # Determine summary signals
    foreign_net = sum(
        e["net_value_idr"] for e in top_buyers + top_sellers if e["type"] == "foreign"
    )
    institutional_net = sum(
        e["net_value_idr"]
        for e in top_buyers + top_sellers
        if e["type"] in ("foreign", "domestic_institutional")
    )

    # With no rows both sums are 0, and `0 > 0` is False -- which used to fall
    # through to "distribution"/"selling". A failed scrape was therefore
    # indistinguishable from a confidently bearish read of real broker data.
    # A verdict is only emitted when there is something to base it on.
    if not (top_buyers or top_sellers):
        return {
            "top_buyers": [],
            "top_sellers": [],
            "summary": {
                "net_broker_flow": None,
                "institutional_bias": None,
                "foreign_broker_bias": None,
                "data_available": False,
                "note": (
                    "no broker rows could be parsed; Stockbit returned nothing usable "
                    "for this ticker. This is an absence of data, not a bearish signal."
                ),
            },
        }

    def _bias(net: float, positive: str, negative: str) -> str:
        if net > 0:
            return positive
        if net < 0:
            return negative
        return "balanced"

    return {
        "top_buyers": top_buyers,
        "top_sellers": top_sellers,
        "summary": {
            "net_broker_flow": _bias(institutional_net, "accumulation", "distribution"),
            "institutional_bias": _bias(institutional_net, "buying", "selling"),
            "foreign_broker_bias": _bias(foreign_net, "buying", "selling"),
            "data_available": True,
        },
    }


async def scrape_foreign_flow(ticker: str | None = None) -> dict:
    """Scrape foreign flow data from Stockbit.

    If ticker is None, attempts to get market-wide foreign flow.
    Returns parsed foreign flow data or raises an exception on failure.
    """
    if ticker:
        url = f"https://stockbit.com/symbol/{ticker}/foreignflow"
    else:
        url = "https://stockbit.com/foreignflow"

    async with httpx.AsyncClient(headers=HEADERS, timeout=15.0, follow_redirects=True) as client:
        await asyncio.sleep(1)  # Politeness delay
        resp = await client.get(url)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")

    foreign_buy = 0
    foreign_sell = 0
    foreign_net = 0

    # Attempt to extract foreign flow numbers from page
    # Look for elements containing flow data
    flow_elements = soup.find_all(["span", "div", "td"], string=True)
    for elem in flow_elements:
        text = elem.get_text(strip=True).lower()
        parent_text = elem.parent.get_text(strip=True).lower() if elem.parent else ""

        try:
            # Try to find numeric values associated with buy/sell labels
            if "foreign buy" in parent_text or "foreign buy" in text:
                val = _extract_number(elem.get_text(strip=True))
                if val is not None:
                    foreign_buy = val
            elif "foreign sell" in parent_text or "foreign sell" in text:
                val = _extract_number(elem.get_text(strip=True))
                if val is not None:
                    foreign_sell = val
            elif "foreign net" in parent_text or "net foreign" in text:
                val = _extract_number(elem.get_text(strip=True))
                if val is not None:
                    foreign_net = val
        except (ValueError, AttributeError):
            continue

    if foreign_net == 0 and (foreign_buy or foreign_sell):
        foreign_net = foreign_buy - foreign_sell

    # Nothing parsed leaves all three at their 0 initialisers, which reads
    # downstream as a measured net of zero. "Neutral flow" and "we could not
    # read the page" are not the same claim.
    return {
        "foreign_buy_idr": foreign_buy,
        "foreign_sell_idr": foreign_sell,
        "foreign_net_idr": foreign_net,
        "data_available": bool(foreign_buy or foreign_sell or foreign_net),
    }


def _extract_number(text: str) -> int | None:
    """Extract a numeric value from text, handling IDR formatting."""
    import re
    # Remove non-numeric chars except minus and decimal
    cleaned = re.sub(r"[^\d\-.,]", "", text)
    cleaned = cleaned.replace(".", "").replace(",", "")
    if cleaned and cleaned != "-":
        try:
            return int(float(cleaned))
        except ValueError:
            return None
    return None
