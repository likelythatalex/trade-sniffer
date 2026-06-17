"""Per-stock normalization → relative features (SPEC §5A, methodology §1). Pure.

Every Wyckoff "high/low volume" or "narrow/wide spread" call is relative to each
stock's own rolling distribution, never an absolute dollar/share figure. This pass
turns a cleaned OHLCV frame into the relative-feature frame the strategy consumes,
so it is reusable by any future strategy.

Degenerate bars (zero range, zero rolling ATR/median) emit **NaN**, not a coerced
value; the strategy treats NaN as "no signal" and never lets it propagate (§6.4).

Conventions (deliberate; flip via calibration if data argues otherwise):
- Rolling windows are trailing and **include the current bar** (matches "rank within
  the window"). Excluding the current bar — so a spike can't dilute its own baseline
  — is the main alternative worth testing later.
- ATR = rolling **mean of True Range** (True Range captures overnight gaps). Simpler
  than Wilder's smoothing and sufficient for a relative spread measure.
- Expected columns are lowercase ``open/high/low/close/volume``; ``data.py`` renames
  yfinance's capitalized columns so the source's naming never leaks downstream.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Only these are needed for the v1 features (open is currently unused).
_REQUIRED_COLUMNS = ("high", "low", "close", "volume")


def compute_features(df: pd.DataFrame, baseline_window: int) -> pd.DataFrame:
    """Compute the relative-feature frame, aligned to ``df``'s index.

    Columns: ``volume_ratio``, ``volume_pctile``, ``spread_atr``, ``spread_pctile``,
    ``close_position`` (see methodology §1). Bars with fewer than ``baseline_window``
    preceding bars are left NaN (unscored — SPEC §5.1 warmup).
    """
    missing = [col for col in _REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"compute_features: df is missing required column(s): {missing}")
    if baseline_window < 1:
        raise ValueError(f"baseline_window must be >= 1 (got {baseline_window}).")

    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    volume = df["volume"].astype(float)

    bar_range = high - low

    # Where the close landed in the bar (0 = low, 1 = high). Zero-range bar -> NaN.
    close_position = _safe_divide(close - low, bar_range)

    # True Range captures gaps: max(range, |high-prev_close|, |low-prev_close|).
    # max(axis=1) skips NaN, so the first bar (no prev_close) falls back to bar_range.
    prev_close = close.shift(1)
    true_range = pd.concat(
        [bar_range, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = true_range.rolling(baseline_window, min_periods=baseline_window).mean()
    spread_atr = _safe_divide(bar_range, atr)

    # Volume vs rolling MEDIAN (median, not mean: volume is right-skewed — one climax
    # bar would inflate a mean and mask the next signal).
    median_volume = volume.rolling(baseline_window, min_periods=baseline_window).median()
    volume_ratio = _safe_divide(volume, median_volume)

    return pd.DataFrame(
        {
            "volume_ratio": volume_ratio,
            "volume_pctile": _rolling_pctile(volume, baseline_window),
            "spread_atr": spread_atr,
            "spread_pctile": _rolling_pctile(bar_range, baseline_window),
            "close_position": close_position,
        },
        index=df.index,
    )


def _safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """Element-wise divide, emitting NaN (not inf, not 0) where the denominator is 0.

    A degenerate bar's dependent feature must *abstain* downstream (SPEC §6.4), so we
    null the denominator at zeros and let NaN flow through the division.
    """
    safe_denominator = denominator.where(denominator != 0, np.nan)
    return numerator / safe_denominator


def _rolling_pctile(series: pd.Series, window: int) -> pd.Series:
    """Percentile rank (0–100) of each bar within its trailing window (current bar
    included): the proportion of window values <= the current value."""

    def percentile_of_last(window_values: np.ndarray) -> float:
        return (np.sum(window_values <= window_values[-1]) / window_values.size) * 100.0

    return series.rolling(window, min_periods=window).apply(percentile_of_last, raw=True)
