"""IDX tick grid, and snapping trade levels onto it.

A level off the grid cannot be an order, so it cannot be a fill. Scoring an
outcome at one reports a trade that was never available.
"""

import json

import pytest

from src.utils.tick import get_tick_size, round_to_tick, snap_levels


class TestTickSize:
    @pytest.mark.parametrize("price,tick", [
        (50, 1.0), (200, 1.0),           # <=200
        (201, 2.0), (500, 2.0),          # 200-500
        (501, 5.0), (2_000, 5.0),        # 500-2000
        (2_001, 10.0), (5_000, 10.0),    # 2000-5000
        (5_001, 25.0), (50_000, 25.0),   # >5000
    ])
    def test_band_boundaries_are_inclusive_at_the_top(self, price, tick):
        assert get_tick_size(price) == tick


class TestRounding:
    def test_a_long_rounds_up_and_a_short_rounds_down(self):
        assert round_to_tick(1_411.41, "long") == 1_415.0
        assert round_to_tick(1_411.41, "short") == 1_410.0

    def test_a_price_already_on_the_grid_is_unchanged(self):
        for price, direction in [(1_365, "long"), (228, "short"), (6_475, "long")]:
            assert round_to_tick(price, direction) == float(price)

    @pytest.mark.parametrize("raw,direction,expected", [
        (199.5, "long", 200.0),      # stays inside the tick-1 band
        (200.5, "long", 202.0),      # lands in tick-2
        (200.5, "short", 200.0),     # crosses down into tick-1
        (1_999.5, "long", 2_000.0),
        (2_000.5, "long", 2_010.0),
        (5_000.5, "short", 5_000.0),
    ])
    def test_rounding_across_a_band_boundary_lands_on_the_new_grid(
        self, raw, direction, expected
    ):
        result = round_to_tick(raw, direction)
        assert result == expected
        assert result % get_tick_size(result) == 0

    def test_non_positive_prices_pass_through(self):
        assert round_to_tick(0) == 0
        assert round_to_tick(None) is None


class TestSnapLevels:
    def test_the_levels_from_the_v3_migration_land_on_the_grid(self):
        """Every level reconstructed for the migration was off-grid."""
        for entry, stop, target in [
            (1_365.0, 1_245.97, 1_411.41),   # AKRA, tick 5
            (228.0, 223.74, 232.26),         # KRAS, tick 2
            (6_475.0, 6_067.08, 6_882.92),   # BBCA, tick 25
        ]:
            snapped = snap_levels(entry, stop, target, "long")
            assert snapped is not None
            for level in snapped:
                assert level % get_tick_size(level) == 0, f"{level} is off-grid"

    def test_ordering_is_preserved_for_both_directions(self):
        e, s, t = snap_levels(1_365.0, 1_245.97, 1_411.41, "long")
        assert s < e < t
        e, s, t = snap_levels(1_365.0, 1_411.41, 1_245.97, "short")
        assert t < e < s

    def test_a_trade_narrower_than_one_tick_is_rejected_not_snapped(self):
        """Both levels land on the same price; the leg has zero width."""
        assert snap_levels(227.1, 226.9, 227.9, "long") is None

    def test_a_pessimistic_snap_never_flatters_a_long(self):
        entry, stop, target = snap_levels(1_002.0, 951.0, 1_098.0, "long")
        assert entry >= 1_002.0    # pays more
        assert stop >= 951.0       # stops sooner
        assert target >= 1_098.0   # further to run


class TestSnappingIsAppliedWhereLevelsEnterTheSystem:
    async def test_declared_levels_are_snapped_when_logged(self, tmp_path, monkeypatch):
        from src.tools import predictions as pr

        log = tmp_path / "predictions_log.json"
        monkeypatch.setattr(pr, "predictions_log_file", lambda: log)

        result = await pr.log_prediction_snapshot(
            ticker="AKRA", strategy_name="test", initial_ev=1.0, ai_win_prob=0.5,
            position_value_idr=10_000_000, profit_target_idr=100_000,
            loss_target_idr=100_000, reasoning="x", target_date="2026-09-30",
            entry_price=1_365.0, stop_loss=1_245.97, target_price=1_411.41,
        )
        assert not result.get("error"), result

        entry = json.loads(log.read_text())[-1]
        assert entry["stop_loss"] == 1_250.0
        assert entry["target_price"] == 1_415.0
        assert entry["levels_snapped_to_tick"] is True

    async def test_a_collapsing_trade_is_rejected_at_log_time(self, tmp_path, monkeypatch):
        from src.tools import predictions as pr

        log = tmp_path / "predictions_log.json"
        monkeypatch.setattr(pr, "predictions_log_file", lambda: log)

        result = await pr.log_prediction_snapshot(
            ticker="KRAS", strategy_name="test", initial_ev=1.0, ai_win_prob=0.5,
            position_value_idr=10_000_000, profit_target_idr=100_000,
            loss_target_idr=100_000, reasoning="x", target_date="2026-09-30",
            entry_price=227.1, stop_loss=226.9, target_price=227.9,
        )
        assert result["error"] is True
        assert "tick" in result["message"].lower()
        assert not log.exists() or json.loads(log.read_text()) == []

    def test_reconstructed_levels_are_snapped(self):
        from src.tools.evaluation import _levels_from_ratios

        entry = {"position_value_idr": 10_000_000, "profit_target_idr": 340_000,
                 "loss_target_idr": 872_000, "direction": "long"}
        e, s, t = _levels_from_ratios(entry, 1_365.0)
        for level in (e, s, t):
            assert level % get_tick_size(level) == 0
