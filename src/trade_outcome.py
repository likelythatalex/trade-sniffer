"""Path-dependent trade outcome — did the stop or the target hit first? (SPEC §8A.2)

A pure evaluator shared by two consumers: the **private journal** (how did a trade I took
play out?) and the future **policy-sweep simulator** (Tier 3 — how would a planner policy
have done?). It walks the forward price path bar by bar and resolves the trade to
``target`` / ``stop`` / ``open``, with realized R and the max favorable/adverse excursion.

This is distinct from ``backtest/outcomes.py`` (which is close-to-close forward returns for
the score's Information Coefficient). A trade plan is path-dependent — a fixed-horizon
return can't see "tagged the stop on day 3, then ran to target" — so this is its own thing.

Conventions (stated, because they bias results):
- **Forward bars only** — the caller passes bars *after* entry, so the entry bar's intrabar
  noise can't resolve the trade on day zero.
- **Conservative tie-break** — if a single bar's range spans BOTH stop and target, the stop
  is counted as hit first. This deliberately under-states results rather than flattering a
  backtest with an optimistic guess about intrabar order.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

_LONG = "long"
_SHORT = "short"


@dataclass(frozen=True)
class TradeOutcome:
    """The resolved (or still-open) outcome of one trade against the forward price path.

    Attributes:
        resolution: ``"target"`` | ``"stop"`` | ``"open"`` (neither hit within the data).
        realized_r: +reward:risk if target hit first, −1.0 if stop hit first, ``None`` if open.
        mfe_r: maximum favorable excursion, in R (how far it ran your way before resolving).
        mae_r: maximum adverse excursion, in R (how far it went against you; ≥ 0).
        bars_held: forward bars evaluated until resolution (or all available, if open).
    """

    resolution: str
    realized_r: float | None
    mfe_r: float
    mae_r: float
    bars_held: int


def evaluate_outcome(
    direction: str, entry: float, stop: float, target: float, forward: pd.DataFrame
) -> TradeOutcome | None:
    """Resolve a trade against ``forward`` OHLC bars (those *after* entry).

    Returns ``None`` (can't evaluate) on a degenerate trade (zero risk, bad direction) or no
    forward data — callers treat that as "no outcome yet", never a crash.
    """
    risk = abs(entry - stop)
    if risk <= 0 or direction not in (_LONG, _SHORT) or forward.empty:
        return None

    reward_risk = abs(target - entry) / risk
    is_long = direction == _LONG
    mfe = mae = 0.0
    resolution, realized, bars_held = "open", None, 0

    for i, (_, bar) in enumerate(forward.iterrows(), start=1):
        high, low = float(bar["high"]), float(bar["low"])
        favorable = (high - entry) if is_long else (entry - low)
        adverse = (entry - low) if is_long else (high - entry)
        mfe = max(mfe, favorable)
        mae = max(mae, adverse)

        hit_stop = (low <= stop) if is_long else (high >= stop)
        hit_target = (high >= target) if is_long else (low <= target)
        bars_held = i
        if hit_stop:  # conservative: a bar spanning both counts the stop first
            resolution, realized = "stop", -1.0
            break
        if hit_target:
            resolution, realized = "target", reward_risk
            break

    return TradeOutcome(resolution, realized, mfe / risk, mae / risk, bars_held)
