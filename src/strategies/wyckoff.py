"""Wyckoff accumulation/distribution strategy — the only strategy in v1 (SPEC §6A).

Detects the structural fingerprints of accumulation/distribution — trading range,
volume behavior (effort vs. result), spring/upthrust — over the RELATIVE features
from ``features.py``, and emits a directional 0-100 conviction score.

Model (first-pass; the score mapping is a CALIBRATION SEED, not final — SPEC §6.4):
each sub-score is a *signed* value in [-100, +100] (+ = accumulation, - = distribution).
The composite is their weighted average via ``sub_weights``; its sign is the direction
and its magnitude the conviction. Within a sub-score, signals start at EQUAL weight
(methodology §5) — i.e. the mean of the firing signals.

A valid trading range is a PRECONDITION: Wyckoff accumulation/distribution happens in
a range, so a chart without one scores 0 / ``none`` rather than flagging on trend
context alone. Signals whose features are NaN simply don't fire (abstain), so the
composite is always finite (NaN never propagates — SPEC §6.4).
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from .base import Strategy, StrategyContext, StrategyResult

# Calibration seeds (could move to config later; kept here as the first-pass defaults).
DIRECTION_FLOOR = 10.0  # |composite| below this -> direction "none"
TREND_FULL_SCALE = 0.20  # net move over trend_lookback that maps to full trend conviction

_SUB_SCORES = ("range_structure", "volume_behavior", "spring_upthrust", "confirmation")


class WyckoffStrategy(Strategy):
    """Range + volume + spring analysis → directional conviction score."""

    name = "wyckoff"

    def evaluate(self, df: pd.DataFrame, context: StrategyContext) -> StrategyResult:
        params = context.params
        features = context.features
        range_info = detect_trading_range(df, features, params)

        # Precondition: no valid range -> not a Wyckoff setup. Score 0, direction none.
        if not range_info["valid"]:
            return StrategyResult(
                direction="none",
                score=0.0,
                sub_scores={name: 0.0 for name in _SUB_SCORES},
                reasons=["no valid trading range"],
                metadata={"range": range_info},
            )

        volume_score, volume_reasons = score_volume_behavior(df, features, range_info, params)
        spring = detect_spring_upthrust(df, features, range_info, params)
        confirmation_score, confirmation_reasons = score_confirmation(df, features, range_info, params)

        sub_scores = {
            "range_structure": _range_structure_score(range_info, params),
            "volume_behavior": volume_score,
            "spring_upthrust": spring["score"],
            "confirmation": confirmation_score,
        }

        weights = params["sub_weights"]
        # sub_weights sum to 100 (validated in config), so this is a weighted average.
        composite_signed = sum(sub_scores[k] * weights[k] for k in _SUB_SCORES) / sum(weights.values())

        if composite_signed >= DIRECTION_FLOOR:
            direction = "accumulation"
        elif composite_signed <= -DIRECTION_FLOOR:
            direction = "distribution"
        else:
            direction = "none"

        reasons = volume_reasons + spring["reasons"] + confirmation_reasons
        return StrategyResult(
            direction=direction,
            score=abs(composite_signed),
            sub_scores=sub_scores,
            reasons=reasons,
            metadata={
                "range": range_info,
                "composite_signed": composite_signed,
                "is_spring": spring["is_spring"],
                "is_upthrust": spring["is_upthrust"],
            },
        )


# --- Pure sub-steps (SPEC §6.1-6.4). Each is independently unit-testable. ---


def detect_trading_range(
    df: pd.DataFrame, features: pd.DataFrame, params: dict[str, Any]
) -> dict[str, Any]:
    """§6.1 — the support/resistance band over the last ``range_lookback`` bars.

    Returns the band, its width (% of mid-price), bar count, validity (tight + long
    enough), and where the last close sits within it. ``position_in_range`` (0 = at
    support, 1 = at resistance) is the single definition of "near support/resistance".
    """
    n = min(int(params["range_lookback"]), len(df))
    window = df.iloc[-n:]
    range_high = float(window["high"].max())
    range_low = float(window["low"].min())
    width = range_high - range_low
    mid = (range_high + range_low) / 2.0
    width_pct = (width / mid * 100.0) if mid > 0 else float("inf")

    valid = (
        n >= int(params["min_range_bars"])
        and width_pct <= float(params["range_max_width_pct"])
        and width > 0
    )

    last_close = float(df["close"].iloc[-1])
    position = (last_close - range_low) / width if width > 0 else float("nan")
    fraction = float(params["range_extreme_fraction"])
    near_support = position <= fraction if pd.notna(position) else False
    near_resistance = position >= (1.0 - fraction) if pd.notna(position) else False

    return {
        "range_high": range_high,
        "range_low": range_low,
        "width": width,
        "width_pct": width_pct,
        "n_bars": n,
        "valid": valid,
        "position_in_range": position,
        "near_support": near_support,
        "near_resistance": near_resistance,
    }


def _range_structure_score(range_info: dict[str, Any], params: dict[str, Any]) -> float:
    """range_structure sub-score: directional bias from where price sits in a valid
    range. Ramps from 0 at the third-boundary to ±100 at the extreme (+ near support)."""
    position = range_info["position_in_range"]
    if pd.isna(position):
        return 0.0
    fraction = float(params["range_extreme_fraction"])
    if range_info["near_support"]:
        proximity = (fraction - position) / fraction  # 1 at support, 0 at boundary
        return 100.0 * _clip01(proximity)
    if range_info["near_resistance"]:
        proximity = (position - (1.0 - fraction)) / fraction
        return -100.0 * _clip01(proximity)
    return 0.0


def score_volume_behavior(
    df: pd.DataFrame, features: pd.DataFrame, range_info: dict[str, Any], params: dict[str, Any]
) -> tuple[float, list[str]]:
    """§6.2 — effort vs. result, No Demand/No Supply, climax. Signed; mean of the
    firing signals (equal-weight seed). + = accumulation, - = distribution."""
    contributions: list[float] = []
    reasons: list[str] = []

    volume_ratio = _last(features, "volume_ratio")
    spread_atr = _last(features, "spread_atr")
    near_support = range_info["near_support"]
    near_resistance = range_info["near_resistance"]
    high_volume = float(params["high_volume_ratio"])
    narrow = float(params["narrow_spread_atr"])

    last_close = float(df["close"].iloc[-1])
    last_open = float(df["open"].iloc[-1])
    is_up_bar = last_close > last_open
    is_down_bar = last_close < last_open

    # Effort vs. result: high volume, narrow spread = absorption/exhaustion at an extreme.
    if pd.notna(volume_ratio) and pd.notna(spread_atr) and volume_ratio >= high_volume and spread_atr <= narrow:
        if near_support:
            contributions.append(100.0)
            reasons.append("absorption at support (high volume, narrow spread)")
        elif near_resistance:
            contributions.append(-100.0)
            reasons.append("supply at resistance (high volume, narrow spread)")

    # No Supply: narrow down-bar on below-median volume at support (bullish).
    if near_support and is_down_bar and pd.notna(volume_ratio) and volume_ratio < 1.0 and pd.notna(spread_atr) and spread_atr <= narrow:
        contributions.append(100.0)
        reasons.append("no supply (narrow down-bar, low volume at support)")
    # No Demand: narrow up-bar on below-median volume at resistance (bearish).
    if near_resistance and is_up_bar and pd.notna(volume_ratio) and volume_ratio < 1.0 and pd.notna(spread_atr) and spread_atr <= narrow:
        contributions.append(-100.0)
        reasons.append("no demand (narrow up-bar, low volume at resistance)")

    # Climax: a genuine volume EXPANSION in the recent window at an extreme. We key off
    # volume_ratio (a real spike) rather than volume_pctile, which ties to ~100 on flat
    # volume and would false-fire. volume_pctile_high stays a config alternative for later.
    climax_window = int(params["climax_window"])
    recent_ratio = features["volume_ratio"].iloc[-climax_window:].max()
    if pd.notna(recent_ratio) and recent_ratio >= high_volume:
        if near_support:
            contributions.append(100.0)
            reasons.append("selling-climax volume near support")
        elif near_resistance:
            contributions.append(-100.0)
            reasons.append("buying-climax volume near resistance")

    score = sum(contributions) / len(contributions) if contributions else 0.0
    return score, reasons


def detect_spring_upthrust(
    df: pd.DataFrame, features: pd.DataFrame, range_info: dict[str, Any], params: dict[str, Any]
) -> dict[str, Any]:
    """§6.3 — spring (false break below support) / upthrust (false break above
    resistance), confirmed by a snapback close back inside the range."""
    n = len(df)
    spring_lookback = int(params["spring_lookback"])
    snapback = int(params["spring_snapback_bars"])
    range_lookback = min(int(params["range_lookback"]), n)

    result = {"score": 0.0, "is_spring": False, "is_upthrust": False, "reasons": []}

    established_end = n - spring_lookback
    established_start = n - range_lookback
    if established_end - established_start < 2:  # need a prior band to break
        return result

    established_support = float(df["low"].iloc[established_start:established_end].min())
    established_resistance = float(df["high"].iloc[established_start:established_end].max())
    recent = df.iloc[established_end:]
    recent_lows = list(recent["low"])
    recent_highs = list(recent["high"])
    recent_closes = list(recent["close"])
    last_close = float(df["close"].iloc[-1])

    # Spring: a recent bar breaks below support, a close snaps back inside within
    # snapback bars, and we are back inside now.
    breaks_down = [i for i, low in enumerate(recent_lows) if low < established_support]
    if breaks_down and last_close >= established_support:
        first = breaks_down[0]
        if any(c >= established_support for c in recent_closes[first : first + snapback + 1]):
            result["is_spring"] = True
            result["score"] = 100.0
            result["reasons"] = ["spring: false break below support, recovered inside"]

    # Upthrust: mirror image at resistance.
    breaks_up = [i for i, high in enumerate(recent_highs) if high > established_resistance]
    if breaks_up and last_close <= established_resistance:
        first = breaks_up[0]
        if any(c <= established_resistance for c in recent_closes[first : first + snapback + 1]):
            result["is_upthrust"] = True
            result["score"] = -100.0
            result["reasons"] = ["upthrust: false break above resistance, rejected"]

    if result["is_spring"] and result["is_upthrust"]:  # ambiguous -> abstain
        result["score"] = 0.0
        result["reasons"] = []
    return result


def score_confirmation(
    df: pd.DataFrame, features: pd.DataFrame, range_info: dict[str, Any], params: dict[str, Any]
) -> tuple[float, list[str]]:
    """§7 confirmation. v1/M1 implements TREND CONTEXT only (signed). RS-vs-SPY and
    MTF agreement need data.py (SPY) and state.py respectively, and volatility
    contraction is non-directional — all abstain here for now (partial by design)."""
    n = len(df)
    if n < 2:
        return 0.0, []
    lookback = min(int(params["trend_lookback"]), n - 1)
    prior_close = float(df["close"].iloc[-1 - lookback])
    last_close = float(df["close"].iloc[-1])
    if prior_close <= 0:
        return 0.0, []

    pct_change = (last_close - prior_close) / prior_close
    # Prior downtrend (pct_change < 0) favors accumulation -> positive.
    signed = _clip(-pct_change / TREND_FULL_SCALE, -1.0, 1.0) * 100.0

    reasons: list[str] = []
    if signed >= DIRECTION_FLOOR:
        reasons.append("prior downtrend (accumulation context)")
    elif signed <= -DIRECTION_FLOOR:
        reasons.append("prior uptrend (distribution context)")
    return signed, reasons


# --- small helpers ---


def _last(features: pd.DataFrame, column: str) -> float:
    return float(features[column].iloc[-1])


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _clip01(value: float) -> float:
    return _clip(value, 0.0, 1.0)
