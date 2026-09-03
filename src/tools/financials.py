"""get_financials tool — Key financial metrics and ratios."""

import asyncio
import logging
import math

import yfinance as yf

from ..utils.cache import TTLCache, cache
from ..utils.completeness import mark_partial
from ..utils.formatting import format_money, safe_pct, safe_round
from ..utils.fx import fx_rate
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


#: Ratios Yahoo forms as (market value in the quote currency) / (statement value
#: in financialCurrency). When an issuer reports in a currency it does not trade
#: in, every one of these is off by exactly the FX rate. ``trailingPE`` is not
#: here: Yahoo's trailing EPS is already in the quote currency.
CURRENCY_MIXED_RATIOS = ("pe_forward", "pb", "ps", "ev_ebitda")


def rescale_valuation(ratios: dict, info: dict) -> tuple[dict, str]:
    """Convert currency-mixed valuation ratios into the quote currency.

    Returns ``(ratios, basis)``. When the rate cannot be established the mixed
    ratios are dropped rather than passed through: a price-to-book of 52,999 is
    not a conservative reading of 3.00, it is a number that will be acted on.
    ``peg`` goes with them — Yahoo does not say what it built it from, and here
    its sibling ``forwardPE`` was 211,999.
    """
    reporting = (info.get("financialCurrency") or "").upper()
    quote = (info.get("currency") or "").upper()
    if not reporting or not quote or reporting == quote:
        return ratios, "reported_currency_matches_quote_currency"

    rate = fx_rate(reporting, quote)
    if not rate:
        for key in (*CURRENCY_MIXED_RATIOS, "peg"):
            ratios[key] = None
        return ratios, f"dropped_unconvertible_{reporting.lower()}_statements"

    for key in CURRENCY_MIXED_RATIOS:
        if ratios.get(key) is not None:
            ratios[key] = ratios[key] / rate
    ratios["peg"] = None
    return ratios, f"rescaled_from_{reporting.lower()}_at_{rate:g}"


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

    # BUMI reports in USD and trades in IDR, so Yahoo returned pb=52999.996.
    _v, valuation_basis = rescale_valuation(
        {"pe_forward": pe_forward, "pb": pb, "ps": ps, "ev_ebitda": ev_ebitda, "peg": peg},
        info,
    )
    pe_forward, pb, ps, ev_ebitda, peg = (
        _v["pe_forward"], _v["pb"], _v["ps"], _v["ev_ebitda"], _v["peg"]
    )
    reporting_currency = (info.get("financialCurrency") or "").upper() or None

    div_yield_pct, div_basis = dividend_yield_pct(info)

    # ── Profitability ─────────────────────────────────────────────────────────
    roe    = _coerce(info.get("returnOnEquity"))
    roa    = _coerce(info.get("returnOnAssets"))
    roe_pct = safe_round(roe * 100, 2) if roe else None
    roa_pct = safe_round(roa * 100, 2) if roa else None

    # ROE and ROA come from `info` and are TTM-based, so they read the same on
    # both periods. ROIC is computed here from the statement column, so on the
    # quarterly report it is a single period's return -- not comparable to the
    # annual figure, and not annualised, because the column may be a quarter or
    # a half and multiplying blindly would invent the difference. Labelled
    # instead of adjusted.
    roic = None
    if operating_income and total_debt is not None and total_equity is not None:
        ic = total_debt + total_equity
        if ic != 0:
            roic = safe_round((operating_income / ic) * 100, 2)
    roic_basis = (
        None if roic is None
        else "annual" if period == "annual"
        else "reporting_period_not_annualized"
    )

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
            "reporting_currency": reporting_currency,
            "revenue": revenue,
            "revenue_formatted": format_money(revenue, reporting_currency),
            "net_income": net_income,
            "net_income_formatted": format_money(net_income, reporting_currency),
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
            "reporting_currency":  reporting_currency,
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
            "valuation_basis":  valuation_basis,
            "dividend_yield_pct": div_yield_pct,
            "dividend_yield_basis": div_basis,
        },
        "profitability": {
            "roe_pct":  roe_pct,
            "roa_pct":  roa_pct,
            "roic_pct": roic,
            "roic_basis": roic_basis,
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

    mark_partial(
        result,
        ("income_statement.revenue", "income_statement.net_income",
         "valuation.pe_ttm", "profitability.roe_pct",
         "cash_flow.operating_cash_flow", "cash_flow.free_cash_flow"),
        "Yahoo did not return every statement line for this ticker. A ratio "
        "derived from a missing line is absent rather than zero.",
    )
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


# Yields above this are not real on IDX; a value that high means the source
# field was misread, so it is dropped rather than reported.
MAX_PLAUSIBLE_YIELD_PCT = 30.0


def dividend_yield_pct(info: dict) -> tuple[float | None, str | None]:
    """Dividend yield as a percentage, plus how it was arrived at.

    yfinance changed the scale of ``dividendYield`` between versions: it used to
    be a fraction (0.0439) and now returns a percentage (4.39). Multiplying by
    100 unconditionally reported ISAT at 439% and DMAS at 829%.

    The field alone cannot be disambiguated inside [0, 1] -- 0.8 is either a
    0.8% yield or an 80% one -- so the rate and price are used where available,
    which needs no inference at all. The heuristic is only a fallback, and it
    says so in the returned basis.
    """
    rate = _coerce(info.get("trailingAnnualDividendRate"))
    spot = _coerce(info.get("currentPrice")) or _coerce(info.get("regularMarketPrice"))
    if rate and spot:
        return safe_round(rate / spot * 100, 2), "trailing_dividend_rate_over_price"

    raw = _coerce(info.get("dividendYield"))
    if not raw:
        return None, None

    pct = safe_round(raw * 100 if raw < 1 else raw, 2)
    if pct is not None and pct > MAX_PLAUSIBLE_YIELD_PCT:
        return None, "implausible_value_discarded"
    return pct, "dividend_yield_field_scale_inferred"
