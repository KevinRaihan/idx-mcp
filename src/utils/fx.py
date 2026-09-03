"""Currency for tools that mix a market price with a financial statement.

An IDX stock always *quotes* in IDR, but many of them *report* in USD — most of
the coal and metals names do. Yahoo hands back both figures without reconciling
them, so any ratio it builds as (market value / statement value) is wrong by
exactly the FX rate. BUMI on 2026-09-03 returned ``priceToBook: 52999.996``:
an IDR share price of 212 divided by a book value of USD 0.004. The true
price-to-book is 3.00.

That is not a rounding problem, it is a factor of ~17,700, and it appears on
every USD-reporting issuer:

    BUMI  financialCurrency=USD  priceToBook=52999.996  -> 3.00
    ADRO  financialCurrency=USD  priceToBook=17062.5    -> 0.96
    ITMG  financialCurrency=USD  priceToBook=15380.15   -> 0.87
    PTBA  financialCurrency=IDR  priceToBook=1.42       -> unchanged
    BBCA  financialCurrency=IDR  priceToBook=3.03       -> unchanged

``trailingPE`` is exempt: Yahoo computes trailing EPS in the quote currency, so
it is already consistent (BUMI 36.36 against a 212 price and 5.83 EPS).
"""

import logging

import yfinance as yf

from .cache import TTLCache, cache

logger = logging.getLogger("idx-mcp.utils.fx")

#: FX moves slowly enough that an hour-old rate cannot change a valuation call.
_TTL_FX = 3_600


def fx_rate(from_ccy: str | None, to_ccy: str | None) -> float | None:
    """Units of ``to_ccy`` per one ``from_ccy``.

    Returns 1.0 when the currencies match and ``None`` when the rate cannot be
    established — never a guess, because a wrong rate here silently rescales
    every valuation ratio rather than failing visibly.
    """
    if not from_ccy or not to_ccy:
        return None
    from_ccy, to_ccy = from_ccy.upper(), to_ccy.upper()
    if from_ccy == to_ccy:
        return 1.0

    pair = f"{from_ccy}{to_ccy}=X"
    cached = cache.get("fx_rate", pair, {})
    if cached is not None:
        return cached

    try:
        info = yf.Ticker(pair).info or {}
        raw = info.get("regularMarketPrice") or info.get("previousClose")
        rate = float(raw) if raw is not None else None
    except Exception as e:
        logger.warning("FX lookup failed for %s: %s", pair, e)
        return None

    if not rate or rate <= 0:
        return None
    cache.set("fx_rate", pair, rate, _TTL_FX, {})
    return rate
