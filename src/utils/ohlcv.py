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

That test only catches the bar Yahoo publishes *before* the open. Once trading
starts, the same row carries a real Open/High/Low/Close that is revised on every
tick, so it passes the NaN filter untouched. Scanners want that row — they are
meant to read the live session. Anything scoring an outcome must not have it:
a high that has only reached 234 by 09:38 is not the session's high, and a
target/stop touch resolved against it can still be contradicted before 16:15.
``drop_unsettled_session`` is the filter for those callers.
"""

from datetime import datetime, time, timedelta, timezone

import pandas as pd

WIB = timezone(timedelta(hours=7))

#: IDX Session 2 ends 16:15 WIB. A bar dated today is only final after this.
MARKET_CLOSE_WIB = time(16, 15)


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


def drop_unsettled_session(
    df: pd.DataFrame, now: datetime | None = None, tz: timezone = WIB
) -> pd.DataFrame:
    """Return ``df`` without any session that has not finished trading.

    Use this in preference to :func:`drop_incomplete_bars` wherever a bar is
    read as a settled fact — outcome scoring, backtests, realized P&L. The
    in-progress session's High and Low are running extremes, not final ones, so
    a stop or target resolved against them is provisional: the opposite level
    can still be touched before the close, which for a long is the difference
    between ``hit_target`` and a pessimistically-scored ``hit_stop``.

    Bars are kept through the last date whose 16:15 WIB close has passed.
    """
    if df is None or df.empty:
        return df

    now = (now or datetime.now(tz)).astimezone(tz)
    settled_through = (
        now.date() if now.time() >= MARKET_CLOSE_WIB else now.date() - timedelta(days=1)
    )

    index = df.index
    if not isinstance(index, pd.DatetimeIndex):
        return df
    return df[[d <= settled_through for d in index.date]]
