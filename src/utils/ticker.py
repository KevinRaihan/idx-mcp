"""Ticker validation and .JK conversion for IDX stocks."""

import re


def validate_ticker(ticker: str) -> str:
    """Validate and normalize an IDX ticker symbol.

    Returns the normalized uppercase ticker (without .JK suffix).
    Raises ValueError if the ticker format is invalid.
    """
    ticker = ticker.strip().upper()

    # Remove .JK suffix if user included it
    if ticker.endswith(".JK"):
        ticker = ticker[:-3]

    # IDX tickers are typically 4 uppercase letters, but some are 3 (e.g., not common)
    # or have numbers. Be permissive: 1-5 alphanumeric chars.
    if not re.match(r"^[A-Z0-9]{1,5}$", ticker):
        raise ValueError(
            f"Invalid IDX ticker format: '{ticker}'. "
            "Expected 1-5 alphanumeric characters (e.g., 'BBCA', 'TLKM')."
        )

    return ticker


def to_yfinance_ticker(ticker: str) -> str:
    """Convert an IDX ticker to yfinance format by appending .JK."""
    normalized = validate_ticker(ticker)
    return f"{normalized}.JK"
