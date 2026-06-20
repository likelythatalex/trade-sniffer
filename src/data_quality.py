"""Detect / repair / exclude bad bars (SPEC §5.2). Pure — no I/O, no network.

Wyckoff lives on volume, so one bad bar can fake a climax. This step is
conservative: it repairs only mechanically-unambiguous issues (drop duplicates,
drop zero/null-volume and null-OHLC bars), excludes the ticker when it can't
(unexplained price spike, split-adjustment mismatch, too few valid bars), and
**never invents data**. Corporate-action data is passed in by ``data.py`` so this
module stays pure and testable.

Runs *before* ``features.py`` so a bad bar can't poison the rolling baseline.

Calendar-based *missing-bar* detection (SPEC §5.2): when ``data.py`` supplies the
expected NYSE sessions (daily timeframe), this module flags sessions with no bar,
forward-fills at most a single isolated one, and measures completeness against the
calendar (not just the bars received). The calendar itself is computed in ``data.py``
so this module stays pure.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .config import DataQualityConfig

# Heuristic constants for the split-adjustment-mismatch check. A close-to-close move
# outside this band with no corporate-action basis looks like an unadjusted split.
_SPIKE_ATR_WINDOW = 14
_SPLIT_GAP_LOW = 0.6
_SPLIT_GAP_HIGH = 1.7

_OHLC = ["open", "high", "low", "close"]


@dataclass
class QualityReport:
    """What the quality pass touched, for the signal log and run summary.

    Attributes:
        excluded: True if the ticker should be skipped this run.
        reason: why it was excluded (or ``None``).
        repairs: human-readable notes on what was repaired (dropped dup bar, etc.).
    """

    excluded: bool = False
    reason: str | None = None
    repairs: list[str] = field(default_factory=list)


def clean(
    df: pd.DataFrame,
    corporate_actions: pd.Series | None,
    config: DataQualityConfig,
    expected_sessions: pd.DatetimeIndex | None = None,
) -> tuple[pd.DataFrame, QualityReport]:
    """Return a cleaned frame + a ``QualityReport``.

    Repairs (mechanical): drop duplicate timestamps, null-OHLC bars, zero/null-volume
    bars; forward-fill a single isolated missing session. Excludes (conservative): an
    unexplained price spike, a split-adjustment mismatch with no corporate-action basis,
    or too few valid bars remaining. ``expected_sessions`` (from ``data.py``, daily only)
    enables calendar-based missing-bar detection; when absent, completeness is measured
    against the bars received.
    """
    report = QualityReport()
    n_input = len(df)
    if n_input == 0:
        report.excluded = True
        report.reason = "empty frame"
        return df, report

    work = df.copy()

    # --- Mechanical repairs ---------------------------------------------------
    if work.index.duplicated().any():
        n = int(work.index.duplicated().sum())
        work = work[~work.index.duplicated(keep="last")]
        report.repairs.append(f"dropped {n} duplicate timestamp(s)")

    null_ohlc = work[_OHLC].isna().any(axis=1)
    if null_ohlc.any():
        work = work[~null_ohlc]
        report.repairs.append(f"dropped {int(null_ohlc.sum())} bar(s) with null OHLC")

    if config.drop_zero_volume_bars:
        bad_volume = work["volume"].isna() | (work["volume"] <= 0)
        if bad_volume.any():
            work = work[~bad_volume]
            report.repairs.append(f"dropped {int(bad_volume.sum())} zero/null-volume bar(s)")

    # --- Exclusions (can't be mechanically repaired) -------------------------
    spikes = _detect_range_spikes(work, config.max_bar_range_atr_mult)
    if spikes.any():
        report.excluded = True
        report.reason = f"unexplained price spike at {work.index[spikes][0]} (range >> ATR)"
        return work, report

    if config.verify_split_adjustment:
        mismatches = _detect_split_mismatch(work, corporate_actions)
        if mismatches.any():
            report.excluded = True
            report.reason = (
                f"split-adjustment mismatch at {work.index[mismatches][0]} "
                f"(large gap, no corporate action)"
            )
            return work, report

    # --- Missing-bar detection (daily; expected sessions supplied by data.py) -
    if expected_sessions is not None and len(expected_sessions) > 0:
        present = set(work.index.normalize())
        missing = [session for session in expected_sessions if session not in present]
        if len(missing) == 1:  # SPEC §5.2: forward-fill at most one isolated missing bar
            work = _forward_fill_session(work, missing[0])
            report.repairs.append(f"forward-filled 1 missing session ({missing[0].date()})")
            present = set(work.index.normalize())
        present_count = sum(1 for session in expected_sessions if session in present)
        n_expected = len(expected_sessions)
        valid_pct = present_count / n_expected * 100.0
        if valid_pct < config.min_valid_bars_pct:
            report.excluded = True
            report.reason = (
                f"only {present_count}/{n_expected} expected sessions present "
                f"({valid_pct:.0f}% < {config.min_valid_bars_pct}%)"
            )
        return work, report

    # Fallback (weekly, or no calendar): completeness vs the bars actually received.
    valid_pct = len(work) / n_input * 100.0
    if valid_pct < config.min_valid_bars_pct:
        report.excluded = True
        report.reason = (
            f"only {len(work)}/{n_input} valid bars ({valid_pct:.0f}% < {config.min_valid_bars_pct}%)"
        )

    return work, report


def _forward_fill_session(df: pd.DataFrame, session: pd.Timestamp) -> pd.DataFrame:
    """Insert a flat carry-forward bar for one missing session: prior close as O/H/L/C,
    zero volume (the most honest 'no data' value — we never invent a price move). Bounded
    to a single isolated session by the caller, per SPEC §5.2."""
    prior = df.index[df.index < session]
    if len(prior) == 0:
        return df  # nothing earlier to carry from; leave the gap
    prev_close = float(df.loc[prior[-1], "close"])
    filled = pd.DataFrame(
        {"open": prev_close, "high": prev_close, "low": prev_close, "close": prev_close, "volume": 0.0},
        index=[session],
    )
    return pd.concat([df, filled]).sort_index()


def _detect_range_spikes(df: pd.DataFrame, max_atr_mult: float) -> pd.Series:
    """Flag bars whose range exceeds ``max_atr_mult`` × the ATR of *preceding* bars.

    ATR is shifted by one bar so a spike never inflates its own baseline and masks
    itself. Splits don't widen intrabar range, so a true range spike is treated as
    bad data with no corporate-action exception.
    """
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr_prev = true_range.rolling(_SPIKE_ATR_WINDOW, min_periods=2).mean().shift(1)
    return atr_prev.notna() & (atr_prev > 0) & ((high - low) > max_atr_mult * atr_prev)


def _detect_split_mismatch(df: pd.DataFrame, corporate_actions: pd.Series | None) -> pd.Series:
    """Flag close-to-close gaps that look like an unadjusted split (no action basis)."""
    close = df["close"]
    prev_close = close.shift(1)
    ratio = close / prev_close
    suspect = prev_close.notna() & (prev_close > 0) & ((ratio < _SPLIT_GAP_LOW) | (ratio > _SPLIT_GAP_HIGH))

    if corporate_actions is None or len(corporate_actions) == 0:
        return suspect
    split_dates = set(corporate_actions.index[corporate_actions != 0])
    explained = pd.Series(df.index.isin(split_dates), index=df.index)
    return suspect & ~explained
