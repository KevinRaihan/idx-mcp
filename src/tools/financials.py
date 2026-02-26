"""get_financials tool — Key financial metrics and ratios."""

import asyncio
import logging

import yfinance as yf

from ..utils.cache import TTLCache, cache
from ..utils.formatting import format_idr, safe_pct, safe_round
from ..utils.ticker import to_yfinance_ticker, validate_ticker
from ..utils.time_utils import format_wib_iso

logger = logging.getLogger("idx-mcp.tools.financials")


def _safe_get(series_or_df, key, default=None):
    """Safely get a value from a pandas Series or DataFrame."""
    try:
        val = series_or_df[key]
        if hasattr(val, "iloc"):
            val = val.iloc[0]
        if val is not None and str(val) != "nan":
            return float(val)
    except (KeyError, IndexError, TypeError, ValueError):
        pass
    return default


def _get_latest_column(df):
    """Get the latest (most recent) column from a financial DataFrame."""
    if df is None or df.empty:
        return None
    return df.columns[0]  # yfinance returns newest first


async def get_financials(ticker: str, period: str = "annual") -> dict:
    """Get key financial metrics and ratios for an IDX stock.

    Args:
        ticker: IDX ticker symbol
        period: "annual" or "quarterly"

    Returns:
        Dict with financial data or error response.
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

    period = period.lower() if period else "annual"
    if period not in ("annual", "quarterly"):
        period = "annual"

    cached = cache.get("get_financials", normalized, {"period": period})
    if cached is not None:
        return cached

    try:
        yf_ticker = to_yfinance_ticker(normalized)
        stock = yf.Ticker(yf_ticker)

        # Fetch all data concurrently — yfinance is blocking so run each in a thread
        if period == "quarterly":
            info, income_stmt, balance, cashflow = await asyncio.gather(
                asyncio.to_thread(lambda: stock.info),
                asyncio.to_thread(lambda: stock.quarterly_financials),
                asyncio.to_thread(lambda: stock.quarterly_balance_sheet),
                asyncio.to_thread(lambda: stock.quarterly_cashflow),
            )
        else:
            info, income_stmt, balance, cashflow = await asyncio.gather(
                asyncio.to_thread(lambda: stock.info),
                asyncio.to_thread(lambda: stock.financials),
                asyncio.to_thread(lambda: stock.balance_sheet),
                asyncio.to_thread(lambda: stock.cashflow),
            )
        info = info or {}

        # Get latest column
        inc_col = _get_latest_column(income_stmt)
        bal_col = _get_latest_column(balance)
        cf_col = _get_latest_column(cashflow)

        reporting_date = str(inc_col.date()) if inc_col is not None else None

        # Income statement
        revenue = _safe_get(income_stmt[inc_col], "Total Revenue") if inc_col is not None else None
        net_income = _safe_get(income_stmt[inc_col], "Net Income") if inc_col is not None else None
        gross_profit = _safe_get(income_stmt[inc_col], "Gross Profit") if inc_col is not None else None
        operating_income = _safe_get(income_stmt[inc_col], "Operating Income") if inc_col is not None else None
        eps = info.get("trailingEps")

        # YoY growth — compare with previous column
        revenue_growth = None
        net_income_growth = None
        if income_stmt is not None and len(income_stmt.columns) >= 2:
            prev_col = income_stmt.columns[1]
            prev_revenue = _safe_get(income_stmt[prev_col], "Total Revenue")
            prev_net_income = _safe_get(income_stmt[prev_col], "Net Income")
            if revenue and prev_revenue and prev_revenue != 0:
                revenue_growth = safe_round(((revenue - prev_revenue) / abs(prev_revenue)) * 100, 2)
            if net_income and prev_net_income and prev_net_income != 0:
                net_income_growth = safe_round(((net_income - prev_net_income) / abs(prev_net_income)) * 100, 2)

        # Margins
        gross_margin = safe_pct(gross_profit, revenue)
        operating_margin = safe_pct(operating_income, revenue)
        net_margin = safe_pct(net_income, revenue)

        # Balance sheet
        total_assets = _safe_get(balance[bal_col], "Total Assets") if bal_col is not None else None
        total_debt = _safe_get(balance[bal_col], "Total Debt") if bal_col is not None else None
        total_equity = _safe_get(balance[bal_col], "Stockholders Equity") if bal_col is not None else None
        if total_equity is None and bal_col is not None:
            total_equity = _safe_get(balance[bal_col], "Total Stockholders Equity")
        cash = _safe_get(balance[bal_col], "Cash And Cash Equivalents") if bal_col is not None else None

        der = safe_round(total_debt / total_equity, 2) if total_debt and total_equity and total_equity != 0 else None

        current_assets = _safe_get(balance[bal_col], "Current Assets") if bal_col is not None else None
        current_liabilities = _safe_get(balance[bal_col], "Current Liabilities") if bal_col is not None else None
        current_ratio = safe_round(current_assets / current_liabilities, 2) if current_assets and current_liabilities and current_liabilities != 0 else None

        # Valuation
        pe_ttm = info.get("trailingPE")
        pe_forward = info.get("forwardPE")
        pb = info.get("priceToBook")
        ps = info.get("priceToSalesTrailing12Months")
        ev_ebitda = info.get("enterpriseToEbitda")
        peg = info.get("pegRatio")
        div_yield = info.get("dividendYield")
        div_yield_pct = safe_round(div_yield * 100, 2) if div_yield else None

        # Profitability
        roe = info.get("returnOnEquity")
        roe_pct = safe_round(roe * 100, 2) if roe else None
        roa = info.get("returnOnAssets")
        roa_pct = safe_round(roa * 100, 2) if roa else None

        roic = None
        if operating_income and total_debt is not None and total_equity is not None:
            invested_capital = total_debt + total_equity
            if invested_capital != 0:
                roic = safe_round((operating_income / invested_capital) * 100, 2)

        # Cash flow
        op_cf = _safe_get(cashflow[cf_col], "Operating Cash Flow") if cf_col is not None else None
        if op_cf is None and cf_col is not None:
            op_cf = _safe_get(cashflow[cf_col], "Total Cash From Operating Activities")
        capex = _safe_get(cashflow[cf_col], "Capital Expenditure") if cf_col is not None else None
        if capex is None and cf_col is not None:
            capex = _safe_get(cashflow[cf_col], "Capital Expenditures")
        fcf = (op_cf + capex) if op_cf is not None and capex is not None else None  # capex is negative

        market_cap = info.get("marketCap")
        fcf_yield = safe_round((fcf / market_cap) * 100, 2) if fcf and market_cap and market_cap != 0 else None

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
                "gross_margin_pct": gross_margin,
                "operating_margin_pct": operating_margin,
                "net_margin_pct": net_margin,
            },
            "balance_sheet": {
                "total_assets": total_assets,
                "total_debt": total_debt,
                "total_equity": total_equity,
                "cash_and_equivalents": cash,
                "der": der,
                "current_ratio": current_ratio,
            },
            "valuation": {
                "pe_ttm": safe_round(pe_ttm, 2),
                "pe_forward": safe_round(pe_forward, 2),
                "pb": safe_round(pb, 2),
                "ps": safe_round(ps, 2),
                "ev_ebitda": safe_round(ev_ebitda, 2),
                "peg": safe_round(peg, 2),
                "dividend_yield_pct": div_yield_pct,
            },
            "profitability": {
                "roe_pct": roe_pct,
                "roa_pct": roa_pct,
                "roic_pct": roic,
            },
            "cash_flow": {
                "operating_cash_flow": op_cf,
                "free_cash_flow": fcf,
                "fcf_yield_pct": fcf_yield,
            },
            "source": "yfinance",
            "data_freshness": f"{'Quarterly' if period == 'quarterly' else 'Annual'} report as of {reporting_date}" if reporting_date else "Unknown",
        }

        cache.set("get_financials", normalized, result, TTLCache.TTL_FUNDAMENTALS, {"period": period})
        return result

    except Exception as e:
        logger.exception(f"Error fetching financials for {normalized}")
        return {
            "error": True,
            "error_type": "data_unavailable",
            "message": f"Failed to fetch financials for {normalized}: {str(e)}",
            "partial_data": None,
            "suggestion": "Try again later. Financial data may not be available for all tickers.",
        }
