"""Expected-value maths.

The regression these lock down: fees used to be charged against the profit and
loss targets rather than the transaction value, which understated IDX friction
by roughly two orders of magnitude on a normal position.
"""

import pytest

from src.tools.predictions import calculate_expected_value


async def test_fees_are_charged_on_transaction_value():
    r = await calculate_expected_value(
        win_prob=0.6,
        profit_target_idr=100_000,
        loss_target_idr=50_000,
        position_value_idr=5_000_000,
    )
    # 5,000,000 * (0.0015 + 0.0025)
    assert r["fees_total_idr"] == pytest.approx(20_000.0)
    assert r["buy_fee_idr"] == pytest.approx(7_500.0)
    assert r["sell_fee_idr"] == pytest.approx(12_500.0)
    assert r["net_win_idr"] == pytest.approx(80_000.0)
    assert r["net_loss_idr"] == pytest.approx(70_000.0)
    # 0.6 * 80_000 - 0.4 * 70_000
    assert r["ev_idr"] == pytest.approx(20_000.0)
    assert r["ev_pct_of_position"] == pytest.approx(0.4)


async def test_fees_scale_with_position_not_with_targets():
    """Doubling the position doubles the fees; the P/L targets do not move them."""
    small = await calculate_expected_value(0.5, 100_000, 100_000, 1_000_000)
    large = await calculate_expected_value(0.5, 100_000, 100_000, 2_000_000)
    assert large["fees_total_idr"] == pytest.approx(2 * small["fees_total_idr"])

    wider_targets = await calculate_expected_value(0.5, 900_000, 900_000, 1_000_000)
    assert wider_targets["fees_total_idr"] == pytest.approx(small["fees_total_idr"])


async def test_breakeven_win_prob_makes_ev_zero():
    r = await calculate_expected_value(0.5, 120_000, 60_000, 4_000_000)
    p_star = r["breakeven_win_prob"]

    at_breakeven = await calculate_expected_value(p_star, 120_000, 60_000, 4_000_000)
    assert at_breakeven["ev_idr"] == pytest.approx(0.0, abs=1e-6)

    assert r["edge_vs_breakeven"] == pytest.approx(0.5 - p_star)


async def test_ev_is_monotonic_in_win_prob():
    evs = [
        (await calculate_expected_value(p / 10, 100_000, 50_000, 5_000_000))["ev_idr"]
        for p in range(0, 11)
    ]
    assert evs == sorted(evs)
    assert evs[0] < 0 < evs[-1]


async def test_zero_fee_rates_reduce_to_raw_expectation():
    r = await calculate_expected_value(
        0.6, 100_000, 50_000, 5_000_000, buy_fee_rate=0.0, sell_fee_rate=0.0
    )
    assert r["fees_total_idr"] == 0.0
    assert r["ev_idr"] == pytest.approx(0.6 * 100_000 - 0.4 * 50_000)


@pytest.mark.parametrize("bad_prob", [-0.01, 1.01, 2.0])
async def test_win_prob_outside_unit_interval_is_rejected(bad_prob):
    r = await calculate_expected_value(bad_prob, 100_000, 50_000, 5_000_000)
    assert r["error"] is True
    assert "win_prob" in r["message"]


@pytest.mark.parametrize("bad_position", [0, -1, None])
async def test_non_positive_position_value_is_rejected(bad_position):
    r = await calculate_expected_value(0.5, 100_000, 50_000, bad_position)
    assert r["error"] is True
    assert "position_value_idr" in r["message"]


async def test_negative_targets_are_rejected():
    """The loss is passed as a positive magnitude; a signed value would flip the EV."""
    r = await calculate_expected_value(0.5, 100_000, -50_000, 5_000_000)
    assert r["error"] is True
    assert "positive magnitudes" in r["message"]
