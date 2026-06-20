"""Tests for the momentum strategy (SPEC §6).

Deterministic close-series fixtures: an uptrend must score bullish (accumulation-aligned), a
downtrend bearish, a flat series abstains, and short/NaN inputs degrade to a finite 0 score.
Exact magnitudes are calibration seeds — assert direction + sign, not pinned values.
"""
from __future__ import annotations

import math

import pandas as pd

from src.strategies.base import StrategyContext
from src.strategies.momentum import MomentumStrategy


def ctx(ma_window: int = 10, roc_window: int = 5) -> StrategyContext:
    return StrategyContext(
        features=pd.DataFrame(), params={"ma_window": ma_window, "roc_window": roc_window}, timeframe="daily"
    )


def df_from_closes(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "open": closes, "high": [c + 1 for c in closes], "low": [c - 1 for c in closes],
        "close": closes, "volume": [100.0] * len(closes),
    })


def test_uptrend_scores_accumulation() -> None:
    closes = [float(100 + i) for i in range(20)]  # steady uptrend
    result = MomentumStrategy().evaluate(df_from_closes(closes), ctx())
    assert result.direction == "accumulation"
    assert result.sub_scores["trend_regime"] > 0 and result.sub_scores["momentum"] > 0
    assert result.metadata["signed"] > 0


def test_downtrend_scores_distribution() -> None:
    closes = [float(120 - i) for i in range(20)]  # steady downtrend
    result = MomentumStrategy().evaluate(df_from_closes(closes), ctx())
    assert result.direction == "distribution"
    assert result.metadata["signed"] < 0


def test_flat_series_is_none() -> None:
    result = MomentumStrategy().evaluate(df_from_closes([100.0] * 20), ctx())
    assert result.direction == "none" and result.score == 0.0


def test_short_history_abstains_with_finite_score() -> None:
    # Fewer bars than ma_window (10) and roc_window (5) -> both components abstain.
    result = MomentumStrategy().evaluate(df_from_closes([100.0, 101.0, 102.0]), ctx())
    assert math.isfinite(result.score) and result.direction == "none"
    assert result.sub_scores == {"trend_regime": 0.0, "momentum": 0.0}


def test_nan_last_bar_yields_finite_score() -> None:
    closes = [float(100 + i) for i in range(20)]
    df = df_from_closes(closes)
    df.loc[df.index[-1], "close"] = float("nan")  # degenerate last bar -> components abstain
    result = MomentumStrategy().evaluate(df, ctx())
    assert math.isfinite(result.score)
