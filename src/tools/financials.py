"""get_financials tool — Key financial metrics and ratios."""

import asyncio
import logging
import math

import yfinance as yf

from ..utils.cache import TTLCache, cache
from ..utils.formatting import format_idr, safe_pct, safe_round
from ..utils.ticker import to_yfinance_ticker, validate_ticker

logger = logging.getLogger("idx-mcp.tools.financials")

_TIMEOUT = 30.0  # seconds for the full gather


def _safe_get(col_series, key, default=None):
    """Safely get a float value from a pandas column-indexed Series.

    Uses math.isnan rather than string comparison so numpy NaN is caught too.
    """
    try:
        val = col_series[key]
        if hasattr(val, "iloc"):
            val = val.iloc[0]
        if val is None:
            return default
        f = float(val)
        return default if math.isnan(f) or math.isinf(f) else f
    except (KeyError, IndexError, TypeError, ValueError):
        return default


def _latest_col(df):
    """Return the most-recent (leftmost) column of a yfinance DataFrame, or None."""
    if df is None or df.empty:
        return None
    return df.columns[0]


async def get_financials(ticker: str, period: str = "annual") -> dict:
    """Get key financial metrics and ratios for an IDX stock."""
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

    period = (period or "annual").lower()
    if period not in ("annual", "quarterly"):
        period = "annual"

    cached = cache.get("get_financials", normalized, {"period": period})
    if cached is not None:
        return cached

    yf_ticker = to_yfinance_ticker(normalized)
    stock = yf.Ticker(yf_ticker)

    # Fetch all four yfinance endpoints concurrently, with a hard timeout
    if period == "quarterly":
        coros = (
            asyncio.to_thread(lambda: stock.info),
            asyncio.to_thread(lambda: stock.quarterly_financials),
            asyncio.to_thread(lambda: stock.quarterly_balance_sheet),
            asyncio.to_thread(lambda: stock.quarterly_cashflow),
        )
    else:
        coros = (
            asyncio.to_thread(lambda: stock.info),
            asyncio.to_thread(lambda: stock.financials),
            asyncio.to_thread(lambda: stock.balance_sheet),
            asyncio.to_thread(lambda: stock.cashflow),
        )

    try:
        info, income_stmt, balance, cashflow = await asyncio.wait_for(
            asyncio.gather(*coros),
            timeout=_TIMEOUT,
        )
    except asyncio.TimeoutError:
        return {
            "error": True,
            "error_type": "timeout",
            "message": f"Financials fetch for {normalized} timed out after {_TIMEOUT:.0f}s.",
            "partial_data": None,
            "suggestion": "Try again in a few seconds.",
        }
    except Exception as e:
        logger.exception(f"Error fetching financials for {normalized}")
        return {
            "error": True,
            "error_type": "data_unavailable",
            "message": f"Failed to fetch financials for {normalized}: {e}",
            "partial_data": None,
            "suggestion": "Try again later. Financial data may not be available for all tickers.",
        }

    info = info or {}

    inc_col = _latest_col(income_stmt)
    bal_col = _latest_col(balance)
    cf_col  = _latest_col(cashflow)

    reporting_date = str(inc_col.date()) if inc_col is not None else None

    # ── Income statement ──────────────────────────────────────────────────────
    def _inc(key):
        return _safe_get(income_stmt[inc_col], key) if inc_col is not None else None

    revenue          = _inc("Total Revenue")
    net_income       = _inc("Net Income")
    gross_profit     = _inc("Gross Profit")
    operating_income = _inc("Operating Income")
    eps              = _coerce(info.get("trailingEps"))

    # YoY growth
    revenue_growth = net_income_growth = None
    if income_stmt is not None and len(income_stmt.columns) >= 2:
        prev_col = income_stmt.columns[1]
        prev_rev = _safe_get(income_stmt[prev_col], "Total Revenue")
        prev_ni  = _safe_get(income_stmt[prev_col], "Net Income")
        if revenue and prev_rev and prev_rev != 0:
            revenue_growth = safe_round(((revenue - prev_rev) / abs(prev_rev)) * 100, 2)
        if net_income and prev_ni and prev_ni != 0:
            net_income_growth = safe_round(((net_income - prev_ni) / abs(prev_ni)) * 100, 2)

    # ── Balance sheet ─────────────────────────────────────────────────────────
    def _bal(key):
        return _safe_get(balance[bal_col], key) if bal_col is not None else None

    total_assets  = _bal("Total Assets")
    total_debt    = _bal("Total Debt")
    total_equity  = _bal("Stockholders Equity") or _bal("Total Stockholders Equity")
    cash          = _bal("Cash And Cash Equivalents")
    current_assets      = _bal("Current Assets")
    current_liabilities = _bal("Current Liabilities")

    der           = safe_round(total_debt / total_equity, 2) if total_debt and total_equity else None
    current_ratio = safe_round(current_assets / current_liabilities, 2) if current_assets and current_liabilities else None

    # ── Valuation (from info) ─────────────────────────────────────────────────
    pe_ttm     = _coerce(info.get("trailingPE"))
    pe_forward = _coerce(info.get("forwardPE"))
    pb         = _coerce(info.get("priceToBook"))
    ps         = _coerce(info.get("priceToSalesTrailing12Months"))
    ev_ebitda  = _coerce(info.get("enterpriseToEbitda"))
    peg        = _coerce(info.get("pegRatio"))
    div_yield  = _coerce(info.get("dividendYield"))
    div_yield_pct = safe_round(div_yield * 100, 2) if div_yield else None

    # ── Profitability ─────────────────────────────────────────────────────────
    roe    = _coerce(info.get("returnOnEquity"))
    roa    = _coerce(info.get("returnOnAssets"))
    roe_pct = safe_round(roe * 100, 2) if roe else None
    roa_pct = safe_round(roa * 100, 2) if roa else None

    roic = None
    if operating_income and total_debt is not None and total_equity is not None:
        ic = total_debt + total_equity
        if ic != 0:
            roic = safe_round((operating_income / ic) * 100, 2)

    # ── Cash flow ─────────────────────────────────────────────────────────────
    def _cf(key):
        return _safe_get(cashflow[cf_col], key) if cf_col is not None else None

    op_cf  = _cf("Operating Cash Flow") or _cf("Total Cash From Operating Activities")
    capex  = _cf("Capital Expenditure") or _cf("Capital Expenditures")
    fcf    = (op_cf + capex) if op_cf is not None and capex is not None else None  # capex negative

    market_cap = _coerce(info.get("marketCap"))
    fcf_yield  = safe_round((fcf / market_cap) * 100, 2) if fcf and market_cap else None

    result = {
        "ticker": normalized,
        "period": period,
        "reporting_date": reporting_date,
        "income_statement": {
            "revenue": revenue,
            "revenue_formatted": format_idr(revenue),
            "net_income": net_income,
            "net_income_formatted": format_idr(net_income),
            "eps": eps,
            "revenue_growth_yoy_pct": revenue_growth,
            "net_income_growth_yoy_pct": net_income_growth,
        },
        "margins": {
            "gross_margin_pct":    safe_pct(gross_profit, revenue),
            "operating_margin_pct": safe_pct(operating_income, revenue),
            "net_margin_pct":      safe_pct(net_income, revenue),
        },
        "balance_sheet": {
            "total_assets":        total_assets,
            "total_debt":          total_debt,
            "total_equity":        total_equity,
            "cash_and_equivalents": cash,
            "der":                 der,
            "current_ratio":       current_ratio,
        },
        "valuation": {
            "pe_ttm":           safe_round(pe_ttm, 2),
            "pe_forward":       safe_round(pe_forward, 2),
            "pb":               safe_round(pb, 2),
            "ps":               safe_round(ps, 2),
            "ev_ebitda":        safe_round(ev_ebitda, 2),
            "peg":              safe_round(peg, 2),
            "dividend_yield_pct": div_yield_pct,
        },
        "profitability": {
            "roe_pct":  roe_pct,
            "roa_pct":  roa_pct,
            "roic_pct": roic,
        },
        "cash_flow": {
            "operating_cash_flow": op_cf,
            "free_cash_flow":      fcf,
            "fcf_yield_pct":       fcf_yield,
        },
        "source": "yfinance",
        "data_freshness": (
            f"{'Quarterly' if period == 'quarterly' else 'Annual'} report as of {reporting_date}"
            if reporting_date else "Unknown"
        ),
    }

    cache.set("get_financials", normalized, result, TTLCache.TTL_FUNDAMENTALS, {"period": period})
    return result


def _coerce(val) -> float | None:
    """Float-coerce with NaN/Inf guard."""
    if val is None:
        return None
    try:
        f = float(val)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None
