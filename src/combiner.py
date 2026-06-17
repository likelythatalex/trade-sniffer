"""Aggregate per-strategy results into the composite score (SPEC §6).

v1 is a weighted average of the per-strategy conviction scores. With one strategy
the composite is just Wyckoff's result. This is the single, designated home for
future correlation-awareness (down-weighting strategies whose scores are
historically correlated) — keep all cross-strategy aggregation here.
"""
from __future__ import annotations

from .strategies.base import StrategyResult


def combine(results: dict[str, StrategyResult], weights: dict[str, float]) -> StrategyResult:
    """Weighted-average the strategies' results into one composite.

    Args:
        results: strategy name -> its ``StrategyResult``.
        weights: strategy name -> composite weight (from ``strategies.*.weight``).

    Returns:
        A composite ``StrategyResult`` (finite score; direction of the strongest
        non-``none`` contributor by weight×score). Sub-scores are namespaced by
        strategy so adding strategies extends, not breaks, the breakdown.
    """
    if not results:
        raise ValueError("combine: no strategy results to aggregate.")
    total_weight = sum(weights.get(name, 0.0) for name in results)
    if total_weight <= 0:
        raise ValueError("combine: strategy weights sum to zero.")

    composite_score = (
        sum(weights.get(name, 0.0) * result.score for name, result in results.items())
        / total_weight
    )

    # Direction = the strongest directional contributor (weight × conviction). For a
    # single strategy this is simply its own direction. Multi-strategy conflict
    # resolution (and correlation-awareness) is future work, and belongs here.
    direction = "none"
    best_strength = 0.0
    for name, result in results.items():
        if result.direction == "none":
            continue
        strength = weights.get(name, 0.0) * result.score
        if strength > best_strength:
            best_strength = strength
            direction = result.direction

    sub_scores = {
        f"{name}.{key}": value
        for name, result in results.items()
        for key, value in result.sub_scores.items()
    }
    reasons = [reason for result in results.values() for reason in result.reasons]

    return StrategyResult(
        direction=direction,
        score=composite_score,
        sub_scores=sub_scores,
        reasons=reasons,
        metadata={"per_strategy": {name: r.direction for name, r in results.items()}},
    )
