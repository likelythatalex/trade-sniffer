"""Strategy abstraction — the one OOP seam in the system (SPEC §6).

Every analysis strategy implements ``Strategy.evaluate`` and returns a
``StrategyResult``. Wyckoff is the only strategy in v1, but this interface lets
future strategies (momentum, relative strength, volatility) slot in without
touching the scanner engine: a new strategy is one new file here plus a line in
``config.yaml``. Everything outside this interface stays plain functions +
dataclasses — this ABC is the only inheritance in the codebase.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class StrategyResult:
    """Normalized output of a single strategy for one ticker/timeframe.

    The combiner consumes these, so every field a strategy emits is on a common
    scale. ``score`` is always finite (NaN must never propagate — SPEC §6.4).

    Attributes:
        direction: ``"accumulation"`` (long), ``"distribution"`` (short), or ``"none"``.
        score: 0-100 conviction, normalized.
        sub_scores: named component scores (e.g. ``range_structure``,
            ``volume_behavior``, ``spring_upthrust``, ``confirmation``).
        reasons: plain-English tags for the dashboard ("volume dry-up at support").
        metadata: anything extra worth surfacing (range bounds, spring bar, ...).
    """

    direction: str
    score: float
    sub_scores: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StrategyContext:
    """Everything a strategy needs beyond the raw OHLCV frame (SPEC §6).

    Passed into ``evaluate`` so strategies stay pure: they never read config,
    re-derive normalization, or do I/O.

    Attributes:
        features: precomputed relative-feature frame from ``features.py`` (§5A),
            aligned to the cleaned OHLCV index.
        params: resolved per-timeframe params (``defaults`` merged with
            ``per_timeframe[tf]``); strategies never merge config themselves (§4.3).
        timeframe: ``"daily"`` or ``"weekly"``.
        prior_state: the *other* timeframe's most recent stored result, for the
            multi-timeframe cross-read (§7.3). ``None`` on cold start → neutral.
        config: the full loaded config, for anything else a strategy needs.
    """

    features: pd.DataFrame
    params: dict[str, Any]
    timeframe: str
    prior_state: Any | None = None
    config: Any | None = None


class Strategy(ABC):
    """Interface every strategy implements. Pure: no I/O, no network calls."""

    #: Registry key; must match the strategy's key under ``strategies:`` in config.
    name: str

    @abstractmethod
    def evaluate(self, df: pd.DataFrame, context: StrategyContext) -> StrategyResult:
        """Score one ticker/timeframe.

        Args:
            df: cleaned raw OHLCV frame (post ``data_quality``).
            context: features, resolved params, timeframe, prior state, config.

        Returns:
            A ``StrategyResult`` with a finite 0-100 score (never NaN).
        """
        raise NotImplementedError
