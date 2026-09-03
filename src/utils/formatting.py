"""IDR formatting and number helpers."""


def format_money(value: float | int | None, currency: str | None = "IDR") -> str | None:
    """Format a large figure with a T / B / M suffix, labelled with its currency.

    The currency is a parameter because IDX issuers do not all report in IDR:
    BUMI's revenue is USD 1.4B, and rendering it as "IDR 1.4B" understated it by
    the FX rate while looking entirely normal.
    """
    if value is None:
        return None

    code = (currency or "IDR").upper()
    abs_val = abs(value)
    sign = "-" if value < 0 else ""

    if abs_val >= 1_000_000_000_000:
        return f"{sign}{code} {abs_val / 1_000_000_000_000:.1f}T"
    elif abs_val >= 1_000_000_000:
        return f"{sign}{code} {abs_val / 1_000_000_000:.1f}B"
    elif abs_val >= 1_000_000:
        return f"{sign}{code} {abs_val / 1_000_000:.1f}M"
    else:
        return f"{sign}{code} {abs_val:,.0f}"


def format_idr(value: float | int | None) -> str | None:
    """IDR-labelled :func:`format_money`. Kept for callers whose figures are IDR."""
    return format_money(value, "IDR")


def format_net_flow(value: float | int | None) -> str | None:
    """Format net flow value with +/- and buy/sell label."""
    if value is None:
        return None

    formatted = format_idr(abs(value))
    if value > 0:
        return f"+{formatted} (net buy)"
    elif value < 0:
        return f"-{formatted} (net sell)"
    else:
        return f"{formatted} (neutral)"


def safe_round(value: float | None, decimals: int = 2) -> float | None:
    """Safely round a value, returning None if input is None."""
    if value is None:
        return None
    return round(value, decimals)


def safe_pct(numerator: float | None, denominator: float | None) -> float | None:
    """Calculate percentage safely, returning None if inputs are None or denominator is zero."""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return round((numerator / denominator) * 100, 2)
