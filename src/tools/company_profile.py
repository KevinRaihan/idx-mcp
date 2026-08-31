"""get_company_profile tool — Company info, ownership, index membership."""

import asyncio
import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf

from ..scrapers.idx import scrape_company_profile
from ..utils.cache import TTLCache, cache
from ..utils.ticker import to_yfinance_ticker, validate_ticker

logger = logging.getLogger("idx-mcp.tools.company_profile")

DATA_DIR = Path(__file__).parent.parent / "data"

_TIMEOUT = 25.0

# Module-level caches for reference JSON data (loaded once on first call)
_conglomerate_map: dict | None = None
_bumn_list:        dict | None = None
_index_members:    dict | None = None


def _load_conglomerate_map() -> dict:
    global _conglomerate_map
    if _conglomerate_map is None:
        with open(DATA_DIR / "conglomerate_map.json", "r", encoding="utf-8") as f:
            _conglomerate_map = json.load(f).get("mapping", {})
    return _conglomerate_map


def _load_bumn_list() -> dict:
    global _bumn_list
    if _bumn_list is None:
        with open(DATA_DIR / "bumn_list.json", "r", encoding="utf-8") as f:
            _bumn_list = json.load(f).get("companies", {})
    return _bumn_list


def _load_index_members() -> dict:
    global _index_members
    if _index_members is None:
        with open(DATA_DIR / "index_members.json", "r", encoding="utf-8") as f:
            _index_members = json.load(f)
    return _index_members


async def get_company_profile(ticker: str) -> dict:
    """Get company profile, ownership structure, and IDX-specific classification."""
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
        result = await asyncio.wait_for(
            _fetch_profile(normalized),
            timeout=_TIMEOUT,
        )
    except asyncio.TimeoutError:
        return {
            "error": True,
            "error_type": "timeout",
            "message": f"Company profile fetch for {normalized} timed out after {_TIMEOUT:.0f}s.",
            "partial_data": {"ticker": normalized},
            "suggestion": "Try again in a few seconds.",
        }
    except Exception as e:
        logger.exception(f"Error fetching profile for {normalized}")
        return {
            "error": True,
            "error_type": "data_unavailable",
            "message": f"Failed to fetch company profile for {normalized}: {e}",
            "partial_data": {"ticker": normalized},
            "suggestion": "Try again later.",
        }

    if not result.get("error"):
        cache.set("get_company_profile", normalized, result, TTLCache.TTL_PROFILE)
    return result


async def _fetch_profile(normalized: str) -> dict:
    """Inner coroutine — fetch all data concurrently then assemble result."""
    yf_ticker = to_yfinance_ticker(normalized)
    stock     = yf.Ticker(yf_ticker)

    # FIX: fetch info, major_holders, institutional_holders and IDX scrape concurrently
    info_coro         = asyncio.to_thread(lambda: stock.info)
    major_holders_coro = asyncio.to_thread(lambda: stock.major_holders)
    inst_holders_coro  = asyncio.to_thread(lambda: stock.institutional_holders)
    idx_coro           = _safe_scrape_idx(normalized)

    info, major_holders_raw, inst_holders_raw, idx_data = await asyncio.gather(
        info_coro, major_holders_coro, inst_holders_coro, idx_coro,
        return_exceptions=True,
    )

    # Normalise errors from gather
    info           = (info or {})           if not isinstance(info, Exception)           else {}
    idx_data       = (idx_data or {})       if not isinstance(idx_data, Exception)       else {}
    major_holders_raw = None if isinstance(major_holders_raw, Exception) else major_holders_raw
    inst_holders_raw  = None if isinstance(inst_holders_raw,  Exception) else inst_holders_raw

    # Load reference data (in-memory after first call)
    conglomerate_map = _load_conglomerate_map()
    bumn_list        = _load_bumn_list()
    index_data       = _load_index_members()

    # ── Basic info ────────────────────────────────────────────────────────────
    name        = info.get("longName") or info.get("shortName") or normalized
    sector      = info.get("sector")
    industry    = info.get("industry")
    website     = info.get("website")
    description = info.get("longBusinessSummary")
    employees   = info.get("fullTimeEmployees")
    city        = info.get("city")

    # ── Ownership ─────────────────────────────────────────────────────────────
    is_bumn     = normalized in bumn_list
    bumn_info   = bumn_list.get(normalized, {})
    conglomerate = conglomerate_map.get(normalized)

    major_shareholders: list[dict] = []

    # `Ticker.major_holders` is not a list of shareholders. yfinance returns four
    # labelled aggregates in a single-column frame, and the positional read took
    # row.iloc[1] as a name -- a column that does not exist -- so every entry
    # came back as {"name": "N/A"}. Worse, institutionsCount is a count that was
    # reported as a percentage: ISAT showed a holder at "90.0" that was really
    # 90 institutions, sitting in the same list as genuine 0.85% figures. Read
    # the labels into named fields instead.
    ownership_breakdown = _parse_major_holders(major_holders_raw)

    # Parse institutional holders
    try:
        if inst_holders_raw is not None and not inst_holders_raw.empty:
            for _, row in inst_holders_raw.head(5).iterrows():
                try:
                    holder_name = row.get("Holder", "Unknown")
                    pct = row.get("pctHeld") or row.get("% Out")
                    if isinstance(pct, (int, float)) and not math.isnan(float(pct)):
                        pct = round(float(pct) * 100, 2)
                    else:
                        pct = None
                    major_shareholders.append({
                        "name": str(holder_name),
                        "pct":  pct,
                        "type": "institutional",
                    })
                except Exception:
                    continue
    except Exception:
        pass

    # Free float
    free_float         = _coerce(info.get("floatShares"))
    shares_outstanding = _coerce(info.get("sharesOutstanding"))
    free_float_pct = (
        round((free_float / shares_outstanding) * 100, 2)
        if free_float and shares_outstanding and shares_outstanding != 0
        else None
    )

    # ── Index membership ──────────────────────────────────────────────────────
    idx30_members = index_data.get("idx30", [])
    lq45_members  = index_data.get("lq45",  [])
    issi_members  = index_data.get("issi",  [])

    # ── Listing date ──────────────────────────────────────────────────────────
    listing_date = idx_data.get("listing_date") or info.get("firstTradeDateEpochUtc")
    if isinstance(listing_date, (int, float)) and not math.isnan(float(listing_date)):
        listing_date = datetime.fromtimestamp(int(listing_date), tz=timezone.utc).strftime("%Y-%m-%d")

    return {
        "ticker":        normalized,
        "name":          name,
        "sector_jasica": idx_data.get("sector") or sector,
        "industry":      industry,
        "listing_date":  listing_date,
        "headquarters":  city or "Jakarta",
        "employees":     employees,
        "website":       website,
        "description":   description[:500] if description else None,
        "ownership": {
            "major_shareholders":    major_shareholders[:5],
            "ownership_breakdown":   ownership_breakdown or None,
            "conglomerate_group":    conglomerate,
            "is_bumn":               is_bumn,
            "government_stake_pct":  bumn_info.get("government_stake_pct") if is_bumn else None,
            "free_float_pct":        free_float_pct,
            "foreign_ownership_pct": None,        # Needs separate data source
            "foreign_ownership_limit_pct": None,
        },
        "index_membership": {
            "lq45":        normalized in lq45_members,
            "idx30":       normalized in idx30_members,
            "issi_syariah": normalized in issi_members,
            "msci_em":     None,  # Needs separate data
            "ftse_em":     None,
        },
        "source": "yfinance + idx.co.id (scraped)",
    }


async def _safe_scrape_idx(ticker: str) -> dict:
    try:
        return await scrape_company_profile(ticker) or {}
    except Exception:
        return {}


_HOLDER_FIELDS = {
    "insiderspercentheld":          ("insiders_pct", True),
    "institutionspercentheld":      ("institutions_pct", True),
    "institutionsfloatpercentheld": ("institutions_float_pct", True),
    "institutionscount":            ("institutions_count", False),
}


def _parse_major_holders(raw) -> dict:
    """Read yfinance's labelled ownership aggregates into named fields.

    Percentages arrive as fractions and are scaled; ``institutionsCount`` is a
    count and is left alone. Unrecognised labels are skipped rather than
    guessed at, so a further change in yfinance's shape yields missing fields
    instead of mislabelled numbers.
    """
    out: dict = {}
    if raw is None or getattr(raw, "empty", True):
        return out
    try:
        col = raw.columns[0]
        for label, value in raw[col].items():
            key = str(label).strip().lower().replace(" ", "").replace("_", "")
            field = _HOLDER_FIELDS.get(key)
            if field is None:
                continue
            name, is_pct = field
            number = _pct_str(value)
            if number is None:
                continue
            out[name] = round(number * 100, 2) if is_pct else int(number)
    except Exception:
        return out
    return out


def _pct_str(val) -> float | None:
    """Parse a percentage string like '47.15%' or a float into a float."""
    try:
        return float(str(val).replace("%", "").strip())
    except (ValueError, TypeError):
        return None


def _coerce(val) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None
