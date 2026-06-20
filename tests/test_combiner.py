"""Tests for the combiner (SPEC §6). v1 = weighted average; trivial for one strategy."""
from __future__ import annotations

import pytest

from src.combiner import combine
from src.strategies.base import Levels, StrategyResult


def test_single_strategy_passes_through() -> None:
    result = StrategyResult(
        direction="accumulation",
        score=80.0,
        sub_scores={"volume_behavior": 70.0},
        reasons=["no supply at support"],
    )
    composite = combine({"wyckoff": result}, {"wyckoff": 1.0})
    assert composite.direction == "accumulation"
    assert composite.score == pytest.approx(80.0)
    # Sub-scores are namespaced so adding strategies extends, not breaks, the breakdown.
    assert composite.sub_scores == {"wyckoff.volume_behavior": 70.0}


def test_direction_from_strongest_contributor() -> None:
    strong_distrib = StrategyResult(direction="distribution", score=90.0)
    weak_accum = StrategyResult(direction="accumulation", score=20.0)
    composite = combine(
        {"a": strong_distrib, "b": weak_accum}, {"a": 1.0, "b": 1.0}
    )
    assert composite.direction == "distribution"


def test_composite_carries_direction_driving_levels() -> None:
    # SPEC §8A: the planner reads composite.levels, so the composite must surface the levels
    # of the strategy that won the direction — not the weaker, opposing one.
    strong = StrategyResult(direction="distribution", score=90.0, levels=Levels(range_high=110.0, range_low=100.0, upthrust_high=114.0))
    weak = StrategyResult(direction="accumulation", score=20.0, levels=Levels(range_high=50.0, range_low=40.0))
    composite = combine({"a": strong, "b": weak}, {"a": 1.0, "b": 1.0})
    assert composite.levels.upthrust_high == 114.0
    assert composite.levels.range_high == 110.0


def test_no_direction_yields_empty_levels() -> None:
    # All "none" -> nothing to plan -> empty levels (planner abstains).
    composite = combine({"a": StrategyResult(direction="none", score=0.0)}, {"a": 1.0})
    assert composite.levels == Levels()


def test_empty_results_raises() -> None:
    with pytest.raises(ValueError, match="no strategy results"):
        combine({}, {})


def test_zero_weights_raises() -> None:
    result = StrategyResult(direction="none", score=0.0)
    with pytest.raises(ValueError, match="sum to zero"):
        combine({"wyckoff": result}, {"wyckoff": 0.0})
