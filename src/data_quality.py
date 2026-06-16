"""Detect / repair / exclude bad bars (SPEC §5.2). Pure — no I/O, no network.

Wyckoff lives on volume, so one bad bar can fake a climax. This step is
conservative: it repairs only mechanically-unambiguous issues, excludes the
ticker when it can't, and **never invents data**. Corporate-action data is passed
in by ``data.py`` so this module stays pure and testable.

Runs *before* ``features.py`` so a bad bar can't poison the rolling baseline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


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
    df: pd.DataFrame, corporate_actions: pd.DataFrame, params: dict[str, Any]
) -> tuple[pd.DataFrame, QualityReport]:
    """Return a cleaned frame + a ``QualityReport``.

    Detect: zero/null volume, price spikes (range > ``max_bar_range_atr_mult`` ×
    ATR), duplicate/missing timestamps, split-adjustment mismatches (vs. the
    passed-in corporate actions). Repair only the unambiguous; otherwise exclude.
    """
    raise NotImplementedError
