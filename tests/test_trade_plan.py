"""Tests for the trade planner (SPEC §8A.1).

Hand-checked fixtures pin the policy (confirmation-break entry, structural+buffer stop,
measured-move target, account-risk sizing) and the abstain paths. Numbers here are derived
by hand from the config seeds so the math is verified, not just exercised.
"""
from __future__ import annotations

import pytest

from src.config import TradePlanConfig
from src.strategies.base import Levels
from src.trade_plan import plan_trade


def cfg(**overrides) -> TradePlanConfig:
    base = dict(
        account_notional=100_000.0,
        risk_pct=1.0,
        stop_buffer_pct=0.5,
        breakeven_at_r=1.0,
        scale_out_pct=50.0,
        trail_atr_mult=2.0,
    )
    base.update(overrides)
    return TradePlanConfig(**base)


# --- Long (accumulation) ------------------------------------------------------


def test_long_plan_entry_stop_target_and_sizing() -> None:
    # Range 100-110, spring poke to 96. Long: enter on break above resistance (110),
    # stop below the spring (96 - 0.5%), target = measured move (110 + range height).
    levels = Levels(range_high=110.0, range_low=100.0, spring_low=96.0)
    plan = plan_trade("accumulation", levels, cfg())
    assert plan is not None
    assert plan.entry == pytest.approx(110.0)              # break above resistance
    assert plan.stop == pytest.approx(96.0 * 0.995)        # spring low - 0.5% buffer = 95.52
    assert plan.target == pytest.approx(110.0 + 10.0)      # entry + range height (110-100)
    assert plan.risk_per_share == pytest.approx(110.0 - 95.52)
    assert plan.reward_per_share == pytest.approx(10.0)
    # Sizing: risk $1,000 (1% of $100k) over the stop distance.
    assert plan.risk_amount == pytest.approx(1000.0)
    assert plan.size_shares == pytest.approx(1000.0 / (110.0 - 95.52))
    assert plan.position_value == pytest.approx(plan.size_shares * 110.0)
    assert plan.reward_risk == pytest.approx(10.0 / (110.0 - 95.52))


def test_long_falls_back_to_range_low_when_no_spring() -> None:
    # No spring detected -> the structural invalidation is the range edge itself.
    plan = plan_trade("accumulation", Levels(range_high=110.0, range_low=100.0), cfg(stop_buffer_pct=0.0))
    assert plan is not None
    assert plan.stop == pytest.approx(100.0)  # range_low, no buffer


# --- Short (distribution) -----------------------------------------------------


def test_short_plan_mirrors_long() -> None:
    # Range 100-110, upthrust to 114. Short: enter on break below support (100),
    # stop above the upthrust (114 + 0.5%), target = measured move down.
    levels = Levels(range_high=110.0, range_low=100.0, upthrust_high=114.0)
    plan = plan_trade("distribution", levels, cfg())
    assert plan is not None
    assert plan.entry == pytest.approx(100.0)              # break below support
    assert plan.stop == pytest.approx(114.0 * 1.005)       # upthrust + 0.5% buffer = 114.57
    assert plan.target == pytest.approx(100.0 - 10.0)      # entry - range height
    assert plan.reward_per_share == pytest.approx(10.0)
    assert plan.size_shares == pytest.approx(1000.0 / (114.57 - 100.0))


# --- Abstain paths (fail soft, never raise) -----------------------------------


def test_abstains_on_no_direction() -> None:
    assert plan_trade("none", Levels(range_high=110.0, range_low=100.0), cfg()) is None


def test_abstains_without_range() -> None:
    assert plan_trade("accumulation", Levels(spring_low=96.0), cfg()) is None


def test_abstains_on_degenerate_range() -> None:
    # Zero-width / inverted band -> no measured move, no plan.
    assert plan_trade("accumulation", Levels(range_high=100.0, range_low=100.0), cfg()) is None


# --- Sizing scales with config -----------------------------------------------


def test_size_scales_with_risk_pct_and_notional() -> None:
    levels = Levels(range_high=110.0, range_low=100.0, spring_low=96.0)
    base = plan_trade("accumulation", levels, cfg())
    bigger = plan_trade("accumulation", levels, cfg(risk_pct=2.0))  # double risk -> double size
    assert bigger.size_shares == pytest.approx(2.0 * base.size_shares)
    assert bigger.risk_amount == pytest.approx(2000.0)


def test_management_playbook_present() -> None:
    plan = plan_trade("accumulation", Levels(range_high=110.0, range_low=100.0, spring_low=96.0), cfg())
    joined = " ".join(plan.management)
    assert "breakeven" in joined and "Scale out 50%" in joined and "Trail" in joined
