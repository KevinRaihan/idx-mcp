"""OHLCV frame hygiene shared by every price-consuming tool.

Yahoo publishes the session that is currently in progress as a trailing row that
already carries a Volume figure while Open/High/Low/Close are still NaN. Nothing
in pandas removes it for you: ``dropna(how="all")`` keeps the row because Volume
is populated.

Left in place, that row is what every tool reads as "today":

* the scanners evaluate ``df.iloc[-1]``, compare NaN against their thresholds,
  and report zero signals across the whole market;
* ``get_technicals`` returns a NaN spot price and a null stochastic, because
  rolling windows propagate the NaN;
* ATR and SMA tails drift for the same reason.

Anchoring on Close is the reliable test: a bar without a close is not a bar that
can be analysed.
"""

import pandas as pd


def drop_incomplete_bars(df: pd.DataFrame, price_col: str = "Close") -> pd.DataFrame:
    """Return ``df`` without rows that lack a usable price.

    Removes fully-empty rows and any row whose price column is NaN, including
    Yahoo's in-progress session bar. The frame is returned unchanged when the
    price column is absent, so callers can apply this defensively.
    """
    if df is None or df.empty:
        return df

    cleaned = df.dropna(how="all")
    if price_col in cleaned.columns:
        cleaned = cleaned[cleaned[price_col].notna()]
    return cleaned
