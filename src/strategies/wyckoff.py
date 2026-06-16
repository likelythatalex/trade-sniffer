"""Wyckoff accumulation/distribution strategy — the only strategy in v1 (SPEC §6A).

Detects the structural fingerprints of accumulation/distribution — trading range,
volume behavior (effort vs. result), spring/upthrust — over the RELATIVE features
from ``features.py`` (never absolute volume/spread levels), and emits a 0-100
conviction score.

Signal definitions and their numeric thresholds live (as ``[VERIFY]``/``[TUNABLE]``
stubs) in ``docs/wyckoff_methodology.md``; sub-score weights come from config
(``wyckoff.sub_weights``). The helpers below are module-level and pure so each can
be unit-tested with a hand-built fixture (SPEC §11).
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from .base import Strategy, StrategyContext, StrategyResult


class WyckoffStrategy(Strategy):
    """Range + volume + spring analysis → directional conviction score."""

    name = "wyckoff"

    def evaluate(self, df: pd.DataFrame, context: StrategyContext) -> StrategyResult:
        # Orchestrates §6.1-6.4: detect range -> score volume/spring/confirmation ->
        # combine via context.params["sub_weights"] -> assign direction.
        raise NotImplementedError("Wyckoff strategy not implemented yet (SPEC §6A).")


# --- Pure sub-steps (SPEC §6.1-6.4). Signatures pinned now; bodies to follow. ---


def detect_trading_range(
    df: pd.DataFrame, features: pd.DataFrame, params: dict[str, Any]
) -> dict[str, Any]:
    """§6.1 — find the most recent consolidation.

    Returns range high/low/width/duration and where price sits within the range.
    The range-position output is the single definition of "near support/resistance"
    used everywhere downstream (lower/upper ``range_extreme_fraction`` of the range).
    """
    raise NotImplementedError


def score_volume_behavior(
    df: pd.DataFrame, features: pd.DataFrame, range_info: dict[str, Any], params: dict[str, Any]
) -> float:
    """§6.2 — effort vs. result over relative features; signed directional score
    (positive = accumulation, negative = distribution). Carries the most weight."""
    raise NotImplementedError


def detect_spring_upthrust(
    df: pd.DataFrame, features: pd.DataFrame, range_info: dict[str, Any], params: dict[str, Any]
) -> dict[str, Any]:
    """§6.3 — spring (false break below support) / upthrust (false break above
    resistance), with volume corroboration per §6.2."""
    raise NotImplementedError
