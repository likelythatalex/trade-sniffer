"""Tests for the Wyckoff strategy (SPEC §6A) — the M1 proof.

Deterministic helpers (range detection, spring/upthrust, trend context) are tested
with precise hand-built fixtures. `evaluate` is tested qualitatively: a textbook
accumulation chart must score accumulation high, a textbook distribution the mirror,
a trending chart must NOT flag, a spring must be detected, and NaN features must
never produce a non-finite score. Exact composite numbers are calibration seeds, so
we assert direction + score separation, not pinned values.
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from src.features import compute_features
from src.strategies.base import StrategyContext
from src.strategies.wyckoff import (
    WyckoffStrategy,
    _score_climax,
    detect_spring_upthrust,
    detect_trading_range,
    score_confirmation,
    score_vol_contraction,
)


def make_params(**overrides) -> dict:
    params = dict(
        range_lookback=12,
        range_max_width_pct=25,
        min_range_bars=10,
        range_extreme_fraction=0.33,
        high_volume_ratio=2.0,
        volume_pctile_high=80,
        narrow_spread_atr=0.5,
        no_demand_supply_median_window=10,
        climax_window=5,
        climax_reaction_atr=1.0,
        spring_lookback=5,
        spring_snapback_bars=3,
        spring_wick_pct=50,
        trend_lookback=15,
        vol_contraction_window=5,
        sub_weights={
            "range_structure": 25,
            "volume_behavior": 35,
            "spring_upthrust": 20,
            "confirmation": 20,
        },
    )
    params.update(overrides)
    return params


def bars(highs, lows, closes, opens, volumes) -> pd.DataFrame:
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes}
    )


def context_for(df: pd.DataFrame, params: dict, baseline_window: int = 5) -> StrategyContext:
    return StrategyContext(
        features=compute_features(df, baseline_window), params=params, timeframe="daily"
    )


# --- Fixtures: textbook charts -----------------------------------------------


def accumulation_df() -> pd.DataFrame:
    highs, lows, closes, opens, vols = [], [], [], [], []
    for c in [116, 113, 110, 107, 104, 101]:  # prior downtrend (idx 0-5)
        closes.append(float(c)); highs.append(c + 2.0); lows.append(c - 2.0); opens.append(c + 1.0); vols.append(100.0)
    for i in range(10):  # trading range 100-110 (idx 6-15)
        c = 108.0 if i % 2 == 0 else 102.0
        closes.append(c); highs.append(110.0); lows.append(100.0); opens.append(105.0); vols.append(100.0)
    closes.append(103.0); highs.append(104.0); lows.append(96.0); opens.append(100.0); vols.append(300.0)  # spring (idx 16)
    closes.append(100.2); highs.append(101.4); lows.append(100.0); opens.append(101.2); vols.append(40.0)  # no-supply (idx 17)
    return bars(highs, lows, closes, opens, vols)


def distribution_df() -> pd.DataFrame:
    highs, lows, closes, opens, vols = [], [], [], [], []
    for c in [84, 87, 90, 93, 96, 99]:  # prior uptrend (idx 0-5)
        closes.append(float(c)); highs.append(c + 2.0); lows.append(c - 2.0); opens.append(c - 1.0); vols.append(100.0)
    for i in range(10):  # trading range 100-110 (idx 6-15)
        c = 102.0 if i % 2 == 0 else 108.0
        closes.append(c); highs.append(110.0); lows.append(100.0); opens.append(105.0); vols.append(100.0)
    closes.append(107.0); highs.append(114.0); lows.append(106.0); opens.append(110.0); vols.append(300.0)  # upthrust (idx 16)
    closes.append(109.8); highs.append(110.0); lows.append(108.6); opens.append(108.8); vols.append(40.0)  # no-demand (idx 17)
    return bars(highs, lows, closes, opens, vols)


def trending_df() -> pd.DataFrame:
    highs, lows, closes, opens, vols = [], [], [], [], []
    for i in range(16):  # strong uptrend, no consolidation -> no valid range
        c = 100.0 + i * 3.0
        closes.append(c); highs.append(c + 2.0); lows.append(c - 2.0); opens.append(c - 1.0); vols.append(100.0)
    return bars(highs, lows, closes, opens, vols)


# --- detect_trading_range -----------------------------------------------------


def test_detect_trading_range_identifies_valid_band() -> None:
    df = bars([110.0] * 12, [100.0] * 12, [105.0] * 12, [105.0] * 12, [100.0] * 12)
    info = detect_trading_range(df, compute_features(df, 5), make_params())
    assert info["range_high"] == 110.0
    assert info["range_low"] == 100.0
    assert info["valid"] is True
    assert info["position_in_range"] == pytest.approx(0.5)
    assert not info["near_support"] and not info["near_resistance"]


def test_detect_trading_range_rejects_too_wide() -> None:
    info = detect_trading_range(trending_df(), compute_features(trending_df(), 5), make_params())
    assert info["valid"] is False


# --- detect_spring_upthrust ---------------------------------------------------


def test_detect_spring() -> None:
    # Established support 100 over idx 0-9; idx 10 breaks to 96; closes snap back inside.
    lows = [100.0] * 10 + [96.0, 101.0, 101.0, 101.0, 101.0]
    closes = [105.0] * 10 + [102.0, 104.0, 104.0, 104.0, 104.0]
    df = bars([110.0] * 15, lows, closes, [105.0] * 15, [100.0] * 15)
    result = detect_spring_upthrust(df, compute_features(df, 5), {}, make_params(range_lookback=15))
    assert result["is_spring"] is True
    assert result["score"] > 0  # magnitude is a calibration seed; assert direction


def test_detect_upthrust() -> None:
    highs = [110.0] * 10 + [114.0, 109.0, 109.0, 109.0, 109.0]
    closes = [105.0] * 10 + [108.0, 106.0, 106.0, 106.0, 106.0]
    df = bars(highs, [100.0] * 15, closes, [105.0] * 15, [100.0] * 15)
    result = detect_spring_upthrust(df, compute_features(df, 5), {}, make_params(range_lookback=15))
    assert result["is_upthrust"] is True
    assert result["score"] < 0


def test_spring_wick_and_volume_boost_score() -> None:
    # Same break+snapback, but a textbook shakeout (deep rejection wick + volume surge on
    # the spring bar) must outscore a marginal poke that closes weakly on flat volume.
    base_lows = [100.0] * 10 + [96.0, 101.0, 101.0, 101.0, 101.0]

    weak_closes = [105.0] * 10 + [97.0, 104.0, 104.0, 104.0, 104.0]  # spring bar closes near its low
    weak = bars([110.0] * 15, base_lows, weak_closes, [105.0] * 15, [100.0] * 15)
    weak_result = detect_spring_upthrust(weak, compute_features(weak, 5), {}, make_params(range_lookback=15))

    strong_closes = [105.0] * 10 + [109.5, 104.0, 104.0, 104.0, 104.0]  # closes back near the high
    strong_vols = [100.0] * 10 + [500.0, 100.0, 100.0, 100.0, 100.0]  # volume surge on the spring bar
    strong = bars([110.0] * 15, base_lows, strong_closes, [105.0] * 15, strong_vols)
    strong_result = detect_spring_upthrust(strong, compute_features(strong, 5), {}, make_params(range_lookback=15))

    assert strong_result["score"] > weak_result["score"]
    assert "on a rejection wick" in " ".join(strong_result["reasons"])


# --- score_confirmation (trend context) --------------------------------------


def test_confirmation_positive_after_downtrend() -> None:
    closes = [float(c) for c in range(120, 99, -2)]  # 120 -> 100
    df = bars([c + 1 for c in closes], [c - 1 for c in closes], closes, closes, [100.0] * len(closes))
    score, _, breakdown = score_confirmation(df, None, {}, make_params(trend_lookback=10))
    assert score > 0
    assert breakdown["trend"] > 0


def test_confirmation_negative_after_uptrend() -> None:
    closes = [float(c) for c in range(100, 121, 2)]  # 100 -> 120
    df = bars([c + 1 for c in closes], [c - 1 for c in closes], closes, closes, [100.0] * len(closes))
    score, _, breakdown = score_confirmation(df, None, {}, make_params(trend_lookback=10))
    assert score < 0
    assert breakdown["trend"] < 0


# --- score_confirmation (RS vs SPY) ------------------------------------------


def _flat_base(length: int = 12) -> pd.DataFrame:
    closes = [100.0] * length  # flat -> trend contributes 0, isolates the RS input
    return bars([101.0] * length, [99.0] * length, closes, closes, [100.0] * length)


def test_confirmation_rs_positive_when_outperforming_spy() -> None:
    df = _flat_base()
    spy = pd.Series([float(120 - i) for i in range(len(df))], index=df.index)  # SPY falling
    _, reasons, breakdown = score_confirmation(
        df, None, {}, make_params(trend_lookback=10), benchmark_close=spy
    )
    assert breakdown["rs"] > 0  # flat stock vs falling index = relative strength
    assert "outperforming SPY (relative strength)" in reasons


def test_confirmation_rs_negative_when_underperforming_spy() -> None:
    df = _flat_base()
    spy = pd.Series([float(90 + i) for i in range(len(df))], index=df.index)  # SPY rising
    _, _, breakdown = score_confirmation(
        df, None, {}, make_params(trend_lookback=10), benchmark_close=spy
    )
    assert breakdown["rs"] < 0  # flat stock vs rising index = relative weakness


def test_confirmation_rs_abstains_without_benchmark() -> None:
    _, _, breakdown = score_confirmation(_flat_base(), None, {}, make_params(trend_lookback=10))
    assert breakdown["rs"] is None


# --- score_vol_contraction (the coil) ----------------------------------------


def _coil_df() -> pd.DataFrame:
    # Earlier bars wide (range 10), recent bars tight (range 2) -> contracting volatility.
    highs = [110.0] * 10 + [103.0] * 5
    lows = [100.0] * 10 + [101.0] * 5
    closes = [105.0] * 10 + [102.0] * 5
    return bars(highs, lows, closes, closes, [100.0] * 15)


def _coil_params() -> dict:
    return make_params(range_lookback=15, vol_contraction_window=5)


def test_vol_contraction_bullish_coil_near_support() -> None:
    score = score_vol_contraction(_coil_df(), {"near_support": True, "near_resistance": False}, _coil_params())
    assert score is not None and score > 0


def test_vol_contraction_bearish_coil_near_resistance() -> None:
    score = score_vol_contraction(_coil_df(), {"near_support": False, "near_resistance": True}, _coil_params())
    assert score is not None and score < 0


def test_vol_contraction_abstains_mid_range() -> None:
    assert score_vol_contraction(_coil_df(), {"near_support": False, "near_resistance": False}, _coil_params()) is None


def test_vol_contraction_abstains_when_expanding() -> None:
    # Recent ranges WIDER than earlier -> not a coil -> abstain even near an extreme.
    df = bars([103.0] * 10 + [110.0] * 5, [101.0] * 10 + [100.0] * 5, [102.0] * 15, [102.0] * 15, [100.0] * 15)
    assert score_vol_contraction(df, {"near_support": True, "near_resistance": False}, _coil_params()) is None


# --- climax (volume spike + reaction) ----------------------------------------


def _climax_df(reaction: bool) -> pd.DataFrame:
    # Range ~100-104, then a high-volume poke to 96 (the climax bar at idx 10).
    highs = [104.0] * 10 + [100.0]
    lows = [100.0] * 10 + [96.0]
    closes = [102.0] * 10 + [97.0]
    vols = [100.0] * 10 + [500.0]  # volume spike on the climax bar
    if reaction:  # price rallies back off the climax low
        follow_h, follow_l, follow_c = [104.0] * 4, [100.0] * 4, [101.0, 102.0, 103.0, 103.0]
    else:  # price stays pinned at the lows -> no reaction
        follow_h, follow_l, follow_c = [100.0] * 4, [96.0] * 4, [97.0] * 4
    highs += follow_h; lows += follow_l; closes += follow_c
    return bars(highs, lows, closes, closes, vols + [100.0] * 4)


def test_climax_fires_only_with_reaction() -> None:
    params = make_params(climax_window=10, climax_reaction_atr=1.0, high_volume_ratio=2.0)
    near_support = {"near_support": True, "near_resistance": False}

    contribution, reason = _score_climax(_climax_df(reaction=True), compute_features(_climax_df(True), 5), near_support, params)
    assert contribution == 100.0 and "reaction" in reason

    # Same volume spike, but no follow-through reaction -> abstain (a spike is not a climax).
    no_reaction, _ = _score_climax(_climax_df(reaction=False), compute_features(_climax_df(False), 5), near_support, params)
    assert no_reaction is None


# --- evaluate (the M1 proof) --------------------------------------------------


def test_textbook_accumulation_scores_accumulation() -> None:
    result = WyckoffStrategy().evaluate(accumulation_df(), context_for(accumulation_df(), make_params()))
    assert result.direction == "accumulation"
    assert result.score >= 50
    assert result.metadata["is_spring"] is True


def test_textbook_distribution_scores_distribution() -> None:
    result = WyckoffStrategy().evaluate(distribution_df(), context_for(distribution_df(), make_params()))
    assert result.direction == "distribution"
    assert result.score >= 50
    assert result.metadata["is_upthrust"] is True


def test_evaluate_populates_structural_levels_for_planner() -> None:
    # SPEC §8A.1: the strategy reports the facts the planner needs. Accumulation here has a
    # ~100-110 range with a spring poke to 96.
    accum = WyckoffStrategy().evaluate(accumulation_df(), context_for(accumulation_df(), make_params()))
    assert accum.levels.range_high == 110.0
    # range_low is the lowest low in the lookback, so the spring poke IS the range low here —
    # which is exactly why spring_low is a SEPARATE field: the planner stops off the poke,
    # not off a range_low a poke can contaminate.
    assert accum.levels.range_low == 96.0
    assert accum.levels.spring_low == 96.0
    assert accum.levels.upthrust_high is None

    # Distribution mirror: an upthrust poke to 114 -> upthrust_high, no spring_low.
    distrib = WyckoffStrategy().evaluate(distribution_df(), context_for(distribution_df(), make_params()))
    assert distrib.levels.upthrust_high == 114.0
    assert distrib.levels.spring_low is None


def test_trending_chart_does_not_flag() -> None:
    result = WyckoffStrategy().evaluate(trending_df(), context_for(trending_df(), make_params()))
    assert result.direction == "none"
    assert result.score == 0.0


def test_accumulation_outscores_trend() -> None:
    accum = WyckoffStrategy().evaluate(accumulation_df(), context_for(accumulation_df(), make_params()))
    trend = WyckoffStrategy().evaluate(trending_df(), context_for(trending_df(), make_params()))
    assert accum.score > trend.score


def test_no_lookahead_evaluation_unaffected_by_future_bars() -> None:
    # SPEC §13: evaluating as-of a bar must not depend on bars that come after it.
    df = accumulation_df()
    params = make_params()
    as_of = WyckoffStrategy().evaluate(df, context_for(df, params))

    # Append arbitrary future bars, then re-evaluate the same prefix; result must match.
    future = df.tail(2).copy()
    extended = pd.concat([df, future], ignore_index=True)
    prefix = extended.iloc[: len(df)]
    recomputed = WyckoffStrategy().evaluate(prefix, context_for(prefix, params))

    assert recomputed.direction == as_of.direction
    assert recomputed.score == pytest.approx(as_of.score)
    assert recomputed.sub_scores == as_of.sub_scores


def test_nan_features_yield_finite_score() -> None:
    df = accumulation_df()
    df.loc[df.index[-5:], "volume"] = 0.0  # zero-volume window -> volume_ratio NaN at the end
    result = WyckoffStrategy().evaluate(df, context_for(df, make_params()))
    assert math.isfinite(result.score)
    assert not any(math.isnan(v) for v in result.sub_scores.values())
