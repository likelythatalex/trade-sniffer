"""Momentum strategy — a trend-following lens, deliberately INDEPENDENT of Wyckoff (SPEC §6).

Wyckoff buys bases (mean-reversion at range extremes); momentum rides established trends. They
disagree by construction — which is exactly what makes momentum a useful *independent* signal
for confirmation stacking (three trend-flavored signals agreeing is one signal counted thrice;
an orthogonal one is real corroboration). It ships at weight 0 (logged but inert) so its
independence/correlation data accrues before calibration decides how — or whether — to weight it.

Model (signed, like every strategy): two equal-weight components, mean of those that fire —
- **trend_regime**: last close vs a simple moving average (above = uptrend, +).
- **momentum**: rate-of-change over a lookback (rising = +).
Positive = bullish = accumulation-aligned (long); negative = bearish = distribution-aligned.
Components abstain (don't fire) on too-short history, so the score is always finite (never NaN).

Pure functions + relative measures only (% vs MA, % ROC) — no absolute thresholds, no I/O.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from .base import Strategy, StrategyContext, StrategyResult

# Calibration seeds (first-pass scales, kept here like Wyckoff's; lookbacks live in config).
DIRECTION_FLOOR = 10.0          # |signed| below this -> direction "none"
TREND_FULL_SCALE = 0.10         # [TUNABLE] price this fraction above/below the MA = full trend
ROC_FULL_SCALE = 0.15           # [TUNABLE] rate-of-change of this magnitude = full momentum

_SUB_SCORES = ("trend_regime", "momentum")


class MomentumStrategy(Strategy):
    """Trend regime + rate-of-change → a signed, directional conviction score."""

    name = "momentum"

    def evaluate(self, df: pd.DataFrame, context: StrategyContext) -> StrategyResult:
        params = context.params
        close = df["close"]

        sub_scores = {name: 0.0 for name in _SUB_SCORES}
        contributions: list[float] = []
        reasons: list[str] = []

        trend = _trend_regime(close, int(params["ma_window"]))
        if trend is not None:
            sub_scores["trend_regime"] = trend
            contributions.append(trend)
            if trend >= DIRECTION_FLOOR:
                reasons.append("uptrend (price above its moving average)")
            elif trend <= -DIRECTION_FLOOR:
                reasons.append("downtrend (price below its moving average)")

        momentum = _rate_of_change(close, int(params["roc_window"]))
        if momentum is not None:
            sub_scores["momentum"] = momentum
            contributions.append(momentum)
            if momentum >= DIRECTION_FLOOR:
                reasons.append("positive momentum")
            elif momentum <= -DIRECTION_FLOOR:
                reasons.append("negative momentum")

        signed = sum(contributions) / len(contributions) if contributions else 0.0
        if signed >= DIRECTION_FLOOR:
            direction = "accumulation"
        elif signed <= -DIRECTION_FLOOR:
            direction = "distribution"
        else:
            direction = "none"

        return StrategyResult(
            direction=direction,
            score=abs(signed),
            sub_scores=sub_scores,
            reasons=reasons,
            metadata={"signed": signed},  # signed composite, for signals.csv (correlation study)
        )


# --- pure components (each abstains -> None on insufficient/degenerate data) ----


def _trend_regime(close: pd.Series, ma_window: int) -> float | None:
    """Signed trend from last close vs its ``ma_window`` SMA, scaled to [-100, +100]."""
    if ma_window <= 0 or len(close) < ma_window:
        return None
    ma = float(close.iloc[-ma_window:].mean())
    last = float(close.iloc[-1])
    if not (ma > 0) or pd.isna(ma) or pd.isna(last):
        return None
    deviation = (last - ma) / ma
    return _clip(deviation / TREND_FULL_SCALE, -1.0, 1.0) * 100.0


def _rate_of_change(close: pd.Series, roc_window: int) -> float | None:
    """Signed rate-of-change over ``roc_window`` bars, scaled to [-100, +100]."""
    if roc_window <= 0 or len(close) <= roc_window:
        return None
    past = float(close.iloc[-1 - roc_window])
    last = float(close.iloc[-1])
    if not (past > 0) or pd.isna(past) or pd.isna(last):
        return None
    roc = (last - past) / past
    return _clip(roc / ROC_FULL_SCALE, -1.0, 1.0) * 100.0


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
