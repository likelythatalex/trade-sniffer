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
class Levels:
    """Structural reference levels a strategy *observed*, for the trade planner (SPEC §8A.1).

    These are **facts** the strategy saw on the chart — the trading-range band and any
    false-break extreme — **not a trade plan.** The planner (`trade_plan.py`) turns these,
    plus the result's ``direction`` and config policy (buffers, measured-move, sizing), into
    entry/stop/target/size. Keeping facts here and policy in the planner is the
    Single-Responsibility split that lets the planner stay strategy-agnostic: any strategy
    that fills in a range gets trade plans with no planner changes. Each field is ``None``
    when the strategy couldn't determine it (the planner abstains on what it's missing).

    Attributes:
        range_high: top of the trading range (resistance band).
        range_low: bottom of the trading range (support band).
        spring_low: deepest false-break low below support → the accumulation invalidation.
        upthrust_high: highest false-break high above resistance → the distribution invalidation.
        atr: recent average true range (price units) — the volatility measure the planner's
            ATR-based stop method uses. ``None`` when not computable.
    """

    range_high: float | None = None
    range_low: float | None = None
    spring_low: float | None = None
    upthrust_high: float | None = None
    atr: float | None = None


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
        levels: structural reference levels for the trade planner (§8A); empty when
            there's no setup (the planner only plans flagged directions anyway).
    """

    direction: str
    score: float
    sub_scores: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    levels: Levels = field(default_factory=Levels)


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
        benchmark_close: the benchmark's (SPY) close series for the relative-strength
            confirmation input (§7.1), aligned by date. ``None`` → RS abstains.
        headlines: recent ``(published_utc, title)`` news items for this ticker, fetched
            upstream (kept out of the pure strategy). ``None`` → the news-sentiment
            strategy abstains. Strategies apply their own as-of (no-lookahead) cutoff.
        insider_transactions: recent insider (Form 4) transactions for this ticker, fetched
            upstream. ``None`` → the insider strategy abstains. As-of cutoff uses each
            transaction's *filing* date (its public-availability moment).
        config: the full loaded config, for anything else a strategy needs.
    """

    features: pd.DataFrame
    params: dict[str, Any]
    timeframe: str
    prior_state: Any | None = None
    benchmark_close: pd.Series | None = None
    headlines: list[tuple[Any, str]] | None = None
    insider_transactions: list[Any] | None = None
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
