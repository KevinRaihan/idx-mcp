"""get_company_profile tool — Company info, ownership, index membership."""

import json
import logging
from pathlib import Path

import yfinance as yf

from ..scrapers.idx import scrape_company_profile
from ..utils.cache import TTLCache, cache
from ..utils.ticker import to_yfinance_ticker, validate_ticker
from ..utils.time_utils import format_wib_iso

logger = logging.getLogger("idx-mcp.tools.company_profile")

DATA_DIR = Path(__file__).parent.parent / "data"

_conglomerate_map: dict | None = None
_bumn_list: dict | None = None
_index_members: dict | None = None


def _load_conglomerate_map() -> dict:
    global _conglomerate_map
    if _conglomerate_map is None:
        path = DATA_DIR / "conglomerate_map.json"
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _conglomerate_map = data.get("mapping", {})
    return _conglomerate_map


def _load_bumn_list() -> dict:
    global _bumn_list
    if _bumn_list is None:
        path = DATA_DIR / "bumn_list.json"
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _bumn_list = data.get("companies", {})
    return _bumn_list


def _load_index_members() -> dict:
    global _index_members
    if _index_members is None:
        path = DATA_DIR / "index_members.json"
        with open(path, "r", encoding="utf-8") as f:
            _index_members = json.load(f)
    return _index_members


async def get_company_profile(ticker: str) -> dict:
    """Get company profile, ownership structure, and IDX-specific classification.

    Args:
        ticker: IDX ticker symbol

    Returns:
        Dict with company profile data or error response.
    """
    try:
        normalized = validate_ticker(ticker)
    except ValueError as e:
        return {
            "error": True,
            "error_type": "invalid_ticker",
            "message": str(e),
            "partial_data": None,
            "suggestion": "Check the ticker symbol.",
        }

    cached = cache.get("get_company_profile", normalized)
    if cached is not None:
        return cached

    try:
        yf_ticker = to_yfinance_ticker(normalized)
        stock = yf.Ticker(yf_ticker)
        info = stock.info or {}

        # Load reference data
        conglomerate_map = _load_conglomerate_map()
        bumn_list = _load_bumn_list()
        index_data = _load_index_members()

        # Basic info from yfinance
        name = info.get("longName") or info.get("shortName") or normalized
        sector = info.get("sector")
        industry = info.get("industry")
        website = info.get("website")
        description = info.get("longBusinessSummary")
        employees = info.get("fullTimeEmployees")
        city = info.get("city")

        # Ownership
        is_bumn = normalized in bumn_list
        bumn_info = bumn_list.get(normalized, {})
        conglomerate = conglomerate_map.get(normalized)

        # Major shareholders from yfinance
        major_shareholders = []
        try:
            holders = stock.major_holders
            if holders is not None and not holders.empty:
                for _, row in holders.iterrows():
                    try:
                        major_shareholders.append({
                            "name": str(row.iloc[1]) if len(row) > 1 else "N/A",
                            "pct": float(str(row.iloc[0]).replace("%", "")) if row.iloc[0] else None,
                        })
                    except (ValueError, IndexError):
                        continue
        except Exception:
            pass

        # Institutional holders
        try:
            inst_holders = stock.institutional_holders
            if inst_holders is not None and not inst_holders.empty:
                for _, row in inst_holders.head(5).iterrows():
                    try:
                        holder_name = row.get("Holder", "Unknown")
                        pct = row.get("pctHeld") or row.get("% Out")
                        if pct and isinstance(pct, (int, float)):
                            pct = round(pct * 100, 2)
                        major_shareholders.append({
                            "name": str(holder_name),
                            "pct": pct,
                            "type": "institutional",
                        })
                    except Exception:
                        continue
        except Exception:
            pass

        # Free float and foreign ownership
        free_float = info.get("floatShares")
        shares_outstanding = info.get("sharesOutstanding")
        free_float_pct = round((free_float / shares_outstanding) * 100, 2) if free_float and shares_outstanding else None

        # Index membership
        idx30_members = index_data.get("idx30", [])
        lq45_members = index_data.get("lq45", [])
        issi_members = index_data.get("issi", [])

        # Try to get additional data from IDX scraping
        idx_data = {}
        try:
            idx_data = await scrape_company_profile(normalized)
        except Exception:
            pass

        listing_date = idx_data.get("listing_date") or info.get("firstTradeDateEpochUtc")
        if isinstance(listing_date, (int, float)):
            from datetime import datetime, timezone
            listing_date = datetime.fromtimestamp(listing_date, tz=timezone.utc).strftime("%Y-%m-%d")

        result = {
            "ticker": normalized,
            "name": name,
            "sector_jasica": idx_data.get("sector") or sector,
            "industry": industry,
            "listing_date": listing_date,
            "headquarters": city or "Jakarta",
            "employees": employees,
            "website": website,
            "description": description[:500] if description else None,
            "ownership": {
                "major_shareholders": major_shareholders[:5] if major_shareholders else [],
                "conglomerate_group": conglomerate,
                "is_bumn": is_bumn,
                "government_stake_pct": bumn_info.get("government_stake_pct") if is_bumn else None,
                "free_float_pct": free_float_pct,
                "foreign_ownership_pct": None,  # Would need separate data source
                "foreign_ownership_limit_pct": None,
            },
            "index_membership": {
                "lq45": normalized in lq45_members,
                "idx30": normalized in idx30_members,
                "issi_syariah": normalized in issi_members,
                "msci_em": None,  # Would need separate data
                "ftse_em": None,
            },
            "source": "yfinance + idx.co.id (scraped)",
        }

        cache.set("get_company_profile", normalized, result, TTLCache.TTL_PROFILE)
        return result

    except Exception as e:
        logger.exception(f"Error fetching profile for {normalized}")
        return {
            "error": True,
            "error_type": "data_unavailable",
            "message": f"Failed to fetch company profile for {normalized}: {str(e)}",
            "partial_data": {"ticker": normalized},
            "suggestion": "Try again later.",
        }
