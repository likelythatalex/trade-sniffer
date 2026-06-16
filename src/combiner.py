"""Aggregate per-strategy results into the composite score (SPEC §6).

v1 is a simple weighted sum (one strategy, so the composite equals Wyckoff's
score). This is the single, designated home for future correlation-awareness
(down-weighting strategies whose scores are historically correlated) — keep all
cross-strategy aggregation here, never scattered into the strategies.
"""
from __future__ import annotations

from .strategies.base import StrategyResult


def combine(results: dict[str, StrategyResult], weights: dict[str, float]) -> StrategyResult:
    """Weighted-sum the enabled strategies' results into one composite.

    Args:
        results: strategy name -> its ``StrategyResult``.
        weights: strategy name -> composite weight (from ``strategies.*.weight``).

    Returns:
        A composite ``StrategyResult`` (finite score, resolved direction).
    """
    raise NotImplementedError
