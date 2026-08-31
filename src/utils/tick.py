"""IDX tick-size grid, and snapping trade levels onto it.

Every price on IDX sits on a piecewise grid whose step widens with price. A
level that is off the grid cannot be an order, so it cannot be a fill either --
and a forward test that resolves an outcome at such a price is reporting a
trade that was never available.

That is not hypothetical. The levels reconstructed for the v3 migration were
all off-grid, and the run on 2026-08-31 reported AKRA filled at exactly
1,411.41 against a tick of 5. It happened not to matter there (the session high
was 1,455, clearing every candidate level), but a target sitting one tick above
the session high scores a win that could not have been taken, and a stop just
inside the low scores a loss that never triggered.

Rounding direction is a policy choice, and this module makes the pessimistic
one, matching how ``resolve_outcome`` treats same-bar ambiguity: every level
moves in the direction that makes the trade look worse.

    long   -> round up:    pay more to enter, stop sits nearer (triggers
                           sooner), target sits further (harder to reach)
    short  -> round down:  sell lower, stop nearer, target further

The alternative -- rounding to nearest -- would let roughly half of all
reconstructed levels drift in the flattering direction, which is exactly the
bias the forward test exists to measure rather than manufacture.
"""

import math


def get_tick_size(price: float) -> float:
    """BEI official piecewise tick size rules (IDX regulation)."""
    if price <= 200:
        return 1.0
    elif price <= 500:
        return 2.0
    elif price <= 2_000:
        return 5.0
    elif price <= 5_000:
        return 10.0
    else:
        return 25.0


def round_to_tick(price: float, direction: str = "long") -> float:
    """Snap ``price`` onto the IDX grid, pessimistically for ``direction``.

    Longs round up, shorts round down. Rounding can push a price across a band
    boundary into a coarser grid, so the result is re-snapped once and the band
    is rechecked; the loop is bounded because each pass moves the price by less
    than one tick of a strictly coarser band.
    """
    if price is None or price <= 0:
        return price

    up = direction != "short"
    result = float(price)
    for _ in range(3):
        tick = get_tick_size(result)
        snapped = (math.ceil(result / tick) if up else math.floor(result / tick)) * tick
        snapped = round(snapped, 4)
        if snapped == result and get_tick_size(snapped) == tick:
            return snapped
        # A price already on the grid of its own band is final.
        if get_tick_size(snapped) == tick:
            return snapped
        result = snapped
    return result


def snap_levels(
    entry: float, stop: float, target: float, direction: str = "long"
) -> tuple[float, float, float] | None:
    """Snap a full trade to the grid, or return ``None`` if it collapses.

    Rounding three levels independently can push two of them onto the same tick
    -- a target and an entry less than one tick apart have nowhere separate to
    land. The resulting "trade" has a zero-width leg and would score as an
    instant win or an instant loss, so it is rejected rather than snapped.
    """
    snapped = tuple(round_to_tick(v, direction) for v in (entry, stop, target))
    e, s, t = snapped
    ordered = s < e < t if direction != "short" else t < e < s
    return snapped if ordered else None
