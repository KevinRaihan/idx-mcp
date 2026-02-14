"""IDX official site scraping for company profiles and market data."""

import asyncio
import logging

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("idx-mcp.scrapers.idx")

HEADERS = {
    "User-Agent": "idx-mcp/1.0 (personal research tool)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
}


async def scrape_company_profile(ticker: str) -> dict:
    """Scrape company profile data from IDX website.

    Returns ownership and listing info, or empty dict on failure.
    """
    url = f"https://www.idx.co.id/id/perusahaan-tercatat/profil-perusahaan-tercatat/?kodeEmiten={ticker}"

    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=15.0, follow_redirects=True) as client:
            await asyncio.sleep(1)
            resp = await client.get(url)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")

        result = {}

        # Try to extract listing date, sector, etc. from profile page
        # IDX page structure varies; we extract what we can
        tables = soup.find_all("table")
        for table in tables:
            rows = table.find_all("tr")
            for row in rows:
                cells = row.find_all(["td", "th"])
                if len(cells) >= 2:
                    label = cells[0].get_text(strip=True).lower()
                    value = cells[1].get_text(strip=True)

                    if "tanggal pencatatan" in label or "listing date" in label:
                        result["listing_date"] = value
                    elif "sektor" in label or "sector" in label:
                        result["sector"] = value
                    elif "sub sektor" in label or "sub sector" in label:
                        result["sub_sector"] = value

        return result

    except Exception as e:
        logger.warning(f"Failed to scrape IDX profile for {ticker}: {e}")
        return {}


async def scrape_sector_indices() -> list[dict]:
    """Scrape sector index performance from IDX.

    Returns list of dicts with sector name and change percentage.
    """
    url = "https://www.idx.co.id/id/data-pasar/ringkasan-perdagangan/ringkasan-saham/"

    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=15.0, follow_redirects=True) as client:
            await asyncio.sleep(1)
            resp = await client.get(url)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")

        sectors = []
        # Attempt to parse sector data from page
        tables = soup.find_all("table")
        for table in tables:
            rows = table.find_all("tr")
            for row in rows:
                cells = row.find_all("td")
                if len(cells) >= 3:
                    sector_name = cells[0].get_text(strip=True)
                    try:
                        change_pct = float(
                            cells[-1].get_text(strip=True).replace("%", "").replace(",", ".")
                        )
                        sectors.append({"sector": sector_name, "change_pct": change_pct})
                    except (ValueError, IndexError):
                        continue

        return sectors

    except Exception as e:
        logger.warning(f"Failed to scrape sector indices: {e}")
        return []
