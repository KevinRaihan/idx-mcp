"""IDR formatting and number helpers."""


def format_idr(value: float | int | None) -> str | None:
    """Format a large IDR number with T (trillion) / B (billion) / M (million) suffix."""
    if value is None:
        return None

    abs_val = abs(value)
    sign = "-" if value < 0 else ""

    if abs_val >= 1_000_000_000_000:
        return f"{sign}IDR {abs_val / 1_000_000_000_000:.1f}T"
    elif abs_val >= 1_000_000_000:
        return f"{sign}IDR {abs_val / 1_000_000_000:.1f}B"
    elif abs_val >= 1_000_000:
        return f"{sign}IDR {abs_val / 1_000_000:.1f}M"
    else:
        return f"{sign}IDR {abs_val:,.0f}"


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
