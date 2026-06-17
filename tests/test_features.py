"""Tests for per-stock feature normalization (SPEC §5A, methodology §1).

Fixtures use tiny baseline windows and hand-checked numbers so every expected
value is verifiable by eye. Covers the cases SPEC §11 calls out: median (not mean)
volume behavior, degenerate bars -> NaN (not a coerced 0/1), warmup bars unscored,
and no-lookahead stability.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.features import compute_features


def test_close_position_locates_close_within_bar() -> None:
    df = pd.DataFrame({"high": [10.0], "low": [0.0], "close": [2.5], "volume": [100.0]})
    feats = compute_features(df, baseline_window=1)
    # (close - low) / (high - low) = 2.5 / 10
    assert feats["close_position"].iloc[0] == pytest.approx(0.25)


def test_volume_ratio_uses_median_not_mean(  ) -> None:
    # Right-skewed volume: a single climax bar. median of [100,100,1000] = 100, so
    # ratio = 1000/100 = 10. If a MEAN (400) were used it would be 2.5 — this asserts
    # the median behavior the methodology requires.
    df = pd.DataFrame(
        {
            "high": [12.0, 12.0, 12.0, 12.0],
            "low": [10.0, 10.0, 10.0, 10.0],
            "close": [11.0, 11.0, 11.0, 11.0],
            "volume": [100.0, 100.0, 1000.0, 100.0],
        }
    )
    feats = compute_features(df, baseline_window=3)
    assert feats["volume_ratio"].iloc[2] == pytest.approx(10.0)


def test_warmup_bars_are_unscored() -> None:
    df = pd.DataFrame(
        {
            "high": [12.0, 12.0, 12.0],
            "low": [10.0, 10.0, 10.0],
            "close": [11.0, 11.0, 11.0],
            "volume": [100.0, 100.0, 1000.0],
        }
    )
    feats = compute_features(df, baseline_window=3)
    # Fewer than baseline_window preceding bars -> undefined (NaN).
    assert feats["volume_ratio"].iloc[:2].isna().all()
    assert feats["volume_ratio"].iloc[2] == pytest.approx(10.0)


def test_spread_atr_divides_range_by_true_range_atr() -> None:
    # TR series = [2, 2, 2, 8]; ATR(window=3): idx2=2, idx3=mean(2,2,8)=4.
    # spread_atr = bar_range / ATR.
    df = pd.DataFrame(
        {
            "high": [12.0, 12.0, 12.0, 18.0],
            "low": [10.0, 10.0, 10.0, 10.0],
            "close": [11.0, 11.0, 11.0, 14.0],
            "volume": [100.0, 100.0, 100.0, 100.0],
        }
    )
    feats = compute_features(df, baseline_window=3)
    assert feats["spread_atr"].iloc[2] == pytest.approx(1.0)  # 2 / 2
    assert feats["spread_atr"].iloc[3] == pytest.approx(2.0)  # 8 / 4
    assert feats["close_position"].iloc[3] == pytest.approx(0.5)  # (14-10)/(18-10)


def test_volume_pctile_is_zero_to_hundred_within_window() -> None:
    climax = pd.DataFrame(
        {
            "high": [12.0, 12.0, 12.0],
            "low": [10.0, 10.0, 10.0],
            "close": [11.0, 11.0, 11.0],
            "volume": [100.0, 100.0, 1000.0],
        }
    )
    feats = compute_features(climax, baseline_window=3)
    # 1000 is the max of the window -> 3/3 * 100 = 100.
    assert feats["volume_pctile"].iloc[2] == pytest.approx(100.0)

    mid = pd.DataFrame(
        {
            "high": [1.0, 1.0, 1.0],
            "low": [0.0, 0.0, 0.0],
            "close": [1.0, 1.0, 1.0],
            "volume": [100.0, 300.0, 200.0],
        }
    )
    feats_mid = compute_features(mid, baseline_window=3)
    # 200 within [100,300,200]: 2 of 3 values <= 200 -> 66.67.
    assert feats_mid["volume_pctile"].iloc[2] == pytest.approx(200.0 / 3.0)


def test_zero_range_bar_nans_only_affected_features() -> None:
    # high == low: close_position (0/0) and spread_atr (ATR=0) are undefined; the
    # volume features are unaffected and still compute (per-feature degradation).
    df = pd.DataFrame(
        {
            "high": [5.0, 5.0, 5.0],
            "low": [5.0, 5.0, 5.0],
            "close": [5.0, 5.0, 5.0],
            "volume": [100.0, 100.0, 100.0],
        }
    )
    feats = compute_features(df, baseline_window=3)
    assert np.isnan(feats["close_position"].iloc[2])
    assert np.isnan(feats["spread_atr"].iloc[2])
    assert feats["volume_ratio"].iloc[2] == pytest.approx(1.0)


def test_zero_volume_window_nans_volume_ratio() -> None:
    # Rolling median volume of 0 -> 0/0 -> NaN, never inf or a coerced 0.
    df = pd.DataFrame(
        {
            "high": [2.0, 2.0, 2.0],
            "low": [0.0, 0.0, 0.0],
            "close": [1.0, 1.0, 1.0],
            "volume": [0.0, 0.0, 0.0],
        }
    )
    feats = compute_features(df, baseline_window=3)
    assert np.isnan(feats["volume_ratio"].iloc[2])


def test_no_lookahead_features_unchanged_by_future_bar() -> None:
    base = pd.DataFrame(
        {
            "high": [12.0, 12.0, 12.0, 18.0, 15.0],
            "low": [10.0, 10.0, 10.0, 10.0, 11.0],
            "close": [11.0, 11.0, 11.0, 14.0, 12.0],
            "volume": [100.0, 100.0, 1000.0, 100.0, 250.0],
        }
    )
    full = compute_features(base, baseline_window=3)
    partial = compute_features(base.iloc[:4], baseline_window=3)
    # Row 3's features must not depend on the appended row 4.
    pd.testing.assert_series_equal(full.iloc[3], partial.iloc[3], check_names=False)


def test_missing_required_column_raises() -> None:
    df = pd.DataFrame({"high": [1.0], "low": [0.0], "close": [0.5]})  # no volume
    with pytest.raises(ValueError, match="volume"):
        compute_features(df, baseline_window=1)
