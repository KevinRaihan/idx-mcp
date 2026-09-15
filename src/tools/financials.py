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


#: How far a column may sit from exactly one year before the latest one and
#: still count as the same period. A leap year moves a period end by a day; the
#: nearest wrong candidate, an adjacent quarter, is about 90 days away.
SAME_PERIOD_TOLERANCE_DAYS = 45


def same_period_last_year(columns, latest):
    """The column covering the period one year before ``latest``, or None.

    Matched by date, never by position. Yahoo's quarterly statements skip
    periods: DMAS came back as Jun-26, Mar-26, Dec-25, Jun-25, Mar-25 with no
    Sep-25, so neither ``columns[1]`` (the previous quarter) nor ``columns[4]``
    (Mar-25) is the year-ago quarter. Only the date says which one is.
    """
    for col in columns:
        try:
            gap_days = (latest - col).days
        except (TypeError, AttributeError):
            continue
        if abs(gap_days - 365) <= SAME_PERIOD_TOLERANCE_DAYS:
            return col
    return None


def yoy_growth(income_stmt) -> tuple[float | None, float | None, str | None]:
    """Revenue and net income growth against the same period a year earlier.

    Returns ``(revenue_growth_pct, net_income_growth_pct, prior_period_end)``.

    This used to compare against ``columns[1]``, which on the quarterly report
    is the previous quarter. DMAS Q2-2026 was published as net income -54.3%
    "YoY" when the year-on-year figure was +382%: Q1 had carried a large land
    sale. When the year-ago period is not in the source, growth is None rather
    than a quarter-on-quarter number under a year-on-year name.
    """
    latest = _latest_col(income_stmt)
    if latest is None:
        return None, None, None
    prior = same_period_last_year(income_stmt.columns[1:], latest)
    if prior is None:
        return None, None, None

    def _growth(key):
        now = _safe_get(income_stmt[latest], key)
        then = _safe_get(income_stmt[prior], key)
        if now is None or not then:
            return None
        return safe_round(((now - then) / abs(then)) * 100, 2)

    prior_end = str(prior.date()) if hasattr(prior, "date") else str(prior)
    return _growth("Total Revenue"), _growth("Net Income"), prior_end


#: Ratios Yahoo may form as (market value in the quote currency) / (statement
#: value in financialCurrency). When an issuer reports in a currency it does not
#: trade in, a mixed one is off by exactly the FX rate. Each is checked before
#: it is converted -- see ``_is_currency_mixed``. ``trailingPE`` is not here:
#: Yahoo's trailing EPS is already in the quote currency.
CURRENCY_MIXED_RATIOS = ("pe_forward", "pb", "ps", "ev_ebitda")

#: With either currency unknown a ratio cannot be checked, only bounded. A
#: USD-over-IDR mix inflates by roughly 17,000, so even a 0.06 price-to-book
#: lands above this, and a genuine reading above it is not usable anyway.
MAX_UNVERIFIED_RATIO = 1000.0


def _is_currency_mixed(key: str, value: float, rate: float | None, info: dict) -> bool:
    """Whether Yahoo formed this ratio across two currencies.

    Yahoo is not consistent about it. BUMI's forwardPE came back as 209,999.98,
    a rupiah price over a dollar EPS, while INCO's was 23.0 because its
    forwardEps of 203.41 is already quoted in rupiah. Dividing every candidate by
    the rate printed INCO, BRPT and ADRO at a forward P/E of 0.0.

    A mixed ratio is inflated by the exchange rate itself, roughly 17,600 for
    USD/IDR, so the two readings sit four orders of magnitude apart. Where Yahoo
    supplies the EPS the forward P/E was built from, whichever reading is closer
    to price over that EPS is taken; otherwise the magnitude decides.
    """
    if key == "pe_forward" and rate:
        eps = _coerce(info.get("forwardEps"))
        spot = _coerce(info.get("currentPrice")) or _coerce(info.get("regularMarketPrice"))
        if eps and eps > 0 and spot:
            implied = spot / eps
            return abs(value / rate - implied) < abs(value - implied)
    return abs(value) > MAX_UNVERIFIED_RATIO


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
    if not reporting or not quote:
        # Unknown is not a match. This path runs whenever Yahoo rate-limits the
        # info call, and it used to report the currencies as matching.
        for key in (*CURRENCY_MIXED_RATIOS, "peg"):
            if ratios.get(key) is not None and abs(ratios[key]) > MAX_UNVERIFIED_RATIO:
                ratios[key] = None
        return ratios, "currency_unverified"
    if reporting == quote:
        return ratios, "reported_currency_matches_quote_currency"

    rate = fx_rate(reporting, quote)
    for key in CURRENCY_MIXED_RATIOS:
        value = ratios.get(key)
        if value is None or not _is_currency_mixed(key, value, rate, info):
            continue
        # Without a rate a mixed ratio cannot be undone, so it is dropped rather
        # than passed through at thousands of times its real size.
        ratios[key] = value / rate if rate else None
    ratios["peg"] = None
    if not rate:
        return ratios, f"dropped_unconvertible_{reporting.lower()}_statements"
    return ratios, f"rescaled_from_{reporting.lower()}_at_{rate:g}"


def trailing_earnings(info: dict) -> tuple[float | None, float | None, str | None]:
    """Trailing EPS and P/E, or None where Yahoo's own fields contradict each other.

    Returns ``(eps, pe_ttm, basis)``. COCO on 2026-09-13 had trailingEps 7.275
    and trailingPE 18.0 while netIncomeToCommon was IDR -198.6B, a loss larger
    than its revenue: the EPS field had not been refreshed since the company was
    profitable, so the P/E priced a profit that no longer exists.

    Signs are compared, not magnitudes. Net income is in the reporting currency
    and EPS in the quote currency, and only the sign survives any exchange rate.
    """
    eps = _coerce(info.get("trailingEps"))
    pe = _coerce(info.get("trailingPE"))
    net_income = _coerce(info.get("netIncomeToCommon"))
    if eps is None:
        return None, None, None
    if net_income and (eps > 0) != (net_income > 0):
        return None, None, "dropped_trailing_eps_contradicts_ttm_net_income"
    if eps <= 0:
        return eps, None, "trailing_twelve_months_no_pe_for_a_loss"
    if net_income is None:
        return eps, pe, "trailing_twelve_months_unchecked"
    return eps, pe, "trailing_twelve_months"


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
    # Trailing twelve months from `info` on both periods, so the quarterly report
    # shows the same EPS as the annual one. Labelled in the payload, not implied.
    eps, pe_ttm, earnings_basis = trailing_earnings(info)

    revenue_growth, net_income_growth, growth_prior_end = yoy_growth(income_stmt)
    growth_basis = (
        None if inc_col is None
        else "same_period_prior_year" if growth_prior_end
        else "prior_year_period_missing_from_source"
    )

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
            "eps_basis": earnings_basis,
            "revenue_growth_yoy_pct": revenue_growth,
            "net_income_growth_yoy_pct": net_income_growth,
            "growth_basis": growth_basis,
            "growth_compared_with_period_ending": growth_prior_end,
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
            "pe_ttm_basis":     earnings_basis,
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
         "income_statement.reporting_currency",
         "valuation.pe_ttm", "profitability.roe_pct",
         "cash_flow.operating_cash_flow", "cash_flow.free_cash_flow"),
        "Yahoo did not return every statement line, or the reporting currency, "
        "for this ticker. A ratio derived from a missing line is absent rather "
        "than zero, and an unknown currency is left unlabelled rather than "
        "assumed to be IDR.",
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

    Two ways of getting this wrong have shipped. yfinance changed
    ``dividendYield`` from a fraction (0.0439) to a percentage (4.39), and
    multiplying by 100 unconditionally reported ISAT at 439% and DMAS at 829%.
    The fix then inferred the scale, treating anything below 1 as a fraction, and
    read BRPT's 0.1% as 10.0%. The yfinance this project runs returns a
    percentage, checked against rate over price on 15-Sep-2026: SSIA's 0.29
    against 5 / 1,730, HRTA's 1.89 against 40 / 2,120. The field is read as that.

    The rate path has its own trap: ``trailingAnnualDividendRate`` is declared in
    the reporting currency, so INCO's USD 0.004 over a 4,680 rupiah price printed
    0.0%. The rate is converted first. A result above the plausibility cap, which
    is what a rate already in rupiah looks like once multiplied by the FX rate,
    falls through to the field instead of being reported.
    """
    rate = _coerce(info.get("trailingAnnualDividendRate"))
    spot = _coerce(info.get("currentPrice")) or _coerce(info.get("regularMarketPrice"))
    if rate and spot:
        reporting = (info.get("financialCurrency") or "").upper()
        quote = (info.get("currency") or "").upper()
        if reporting and quote and reporting != quote:
            fx = fx_rate(reporting, quote)
            rate = rate * fx if fx else None
        if rate:
            pct = safe_round(rate / spot * 100, 2)
            if pct is not None and pct <= MAX_PLAUSIBLE_YIELD_PCT:
                return pct, "trailing_dividend_rate_over_price"

    raw = _coerce(info.get("dividendYield"))
    if not raw:
        return None, None

    pct = safe_round(raw, 2)
    if pct is not None and pct > MAX_PLAUSIBLE_YIELD_PCT:
        return None, "implausible_value_discarded"
    return pct, "dividend_yield_field_percent"
