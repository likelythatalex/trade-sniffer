"""Tests for the data-quality pass (SPEC §5.2). Pure — hand-built fixtures only.

Covers SPEC §11: zero-volume bar, price spike, duplicate timestamp, a frame that
should be excluded, plus the split-adjustment mismatch (explained vs unexplained).
"""
from __future__ import annotations

import dataclasses

import pandas as pd
import pytest

from src.config import DataQualityConfig
from src.data_quality import clean


def make_ohlcv(n: int = 40, price: float = 100.0, rng: float = 2.0, volume: float = 100.0) -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-01", periods=n)
    close = pd.Series([price] * n, index=idx, dtype=float)
    return pd.DataFrame(
        {
            "open": close,
            "high": close + rng / 2,
            "low": close - rng / 2,
            "close": close,
            "volume": [float(volume)] * n,
        },
        index=idx,
    )


def dq_config(**overrides) -> DataQualityConfig:
    base = DataQualityConfig(
        max_bar_range_atr_mult=8.0,
        min_valid_bars_pct=95.0,
        drop_zero_volume_bars=True,
        verify_split_adjustment=True,
        real_move_volume_mult=1.5,
    )
    return dataclasses.replace(base, **overrides)


def test_clean_frame_passes_through_untouched() -> None:
    df = make_ohlcv()
    cleaned, report = clean(df, None, dq_config())
    assert report.excluded is False
    assert report.repairs == []
    assert len(cleaned) == len(df)


def test_zero_volume_bar_is_dropped() -> None:
    df = make_ohlcv()
    df.loc[df.index[10], "volume"] = 0.0
    cleaned, report = clean(df, None, dq_config())
    assert len(cleaned) == len(df) - 1
    assert report.excluded is False
    assert any("zero" in r for r in report.repairs)


def test_duplicate_timestamp_is_dropped() -> None:
    df = make_ohlcv()
    dup = df.iloc[[10]]  # same index label -> duplicate timestamp
    df = pd.concat([df, dup]).sort_index()
    cleaned, report = clean(df, None, dq_config())
    assert not cleaned.index.duplicated().any()
    assert any("duplicate" in r for r in report.repairs)


def test_price_spike_excludes_ticker() -> None:
    df = make_ohlcv()
    df.loc[df.index[20], "high"] = 150.0  # range ~51 vs ATR ~2 -> spike
    cleaned, report = clean(df, None, dq_config())
    assert report.excluded is True
    assert "spike" in (report.reason or "")


def test_high_volume_spike_is_kept_as_real_move() -> None:
    # Same large range as the spike test, but on heavy volume -> a real move, not a glitch.
    df = make_ohlcv()
    df.loc[df.index[20], "high"] = 150.0
    df.loc[df.index[20], "volume"] = 1000.0  # ~10x the baseline of 100
    cleaned, report = clean(df, None, dq_config())
    assert report.excluded is False
    assert any("heavy volume" in r for r in report.repairs)


def test_high_volume_split_gap_is_kept_as_real_move() -> None:
    # A real earnings/M&A gap (no split) on heavy volume must not be excluded.
    df = make_ohlcv()
    df.loc[df.index[20:], ["open", "high", "low", "close"]] /= 2.0  # ~50% gap
    df.loc[df.index[20], "volume"] = 1000.0  # heavy volume on the gap bar
    cleaned, report = clean(df, None, dq_config())
    assert report.excluded is False


def test_unexplained_split_gap_excludes_ticker() -> None:
    df = make_ohlcv()
    df.loc[df.index[20:], ["open", "high", "low", "close"]] /= 2.0  # ~50% gap, no split given
    cleaned, report = clean(df, None, dq_config())
    assert report.excluded is True
    assert "split" in (report.reason or "")


def test_split_gap_explained_by_corporate_action_is_kept() -> None:
    df = make_ohlcv()
    df.loc[df.index[20:], ["open", "high", "low", "close"]] /= 2.0
    splits = pd.Series({df.index[20]: 2.0})  # a 2:1 split on that date explains the gap
    cleaned, report = clean(df, splits, dq_config())
    assert report.excluded is False


def test_too_many_bad_bars_excludes_ticker() -> None:
    df = make_ohlcv(n=40)
    df.loc[df.index[:10], "volume"] = 0.0  # 10/40 dropped -> 75% valid < 95%
    cleaned, report = clean(df, None, dq_config())
    assert report.excluded is True
    assert "valid bars" in (report.reason or "")


def test_empty_frame_excluded() -> None:
    cleaned, report = clean(pd.DataFrame(), None, dq_config())
    assert report.excluded is True


# --- calendar-based missing-bar detection ------------------------------------


def test_single_missing_session_is_forward_filled() -> None:
    df = make_ohlcv(n=20)
    expected = df.index  # every business day is an expected session
    with_gap = df.drop(df.index[10])  # one isolated session missing
    cleaned, report = clean(with_gap, None, dq_config(), expected_sessions=expected)
    assert report.excluded is False
    assert any("forward-filled" in r for r in report.repairs)
    assert df.index[10] in cleaned.index  # the gap was filled
    assert cleaned.loc[df.index[10], "volume"] == 0.0  # honest "no data" bar


def test_too_many_missing_sessions_excludes_ticker() -> None:
    df = make_ohlcv(n=20)
    expected = df.index
    with_gaps = df.drop(df.index[5:10])  # 5/20 sessions missing -> 75% < 95%
    cleaned, report = clean(with_gaps, None, dq_config(), expected_sessions=expected)
    assert report.excluded is True
    assert "expected sessions" in (report.reason or "")


def test_complete_series_vs_calendar_not_excluded() -> None:
    df = make_ohlcv(n=20)
    cleaned, report = clean(df, None, dq_config(), expected_sessions=df.index)
    assert report.excluded is False
    assert report.repairs == []
