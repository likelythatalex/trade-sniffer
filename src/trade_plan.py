"""Trade planner — turns a flagged signal into a SUGGESTED, never-executed plan (SPEC §8A.1).

Pure + strategy-agnostic: given a direction, the structural ``Levels`` a strategy observed,
and config policy, it derives entry / stop / target / size + a management playbook. It reads
only ``Levels`` (never strategy internals), so any future strategy that fills in a range gets
plans with no changes here — and it **never places a trade** (the tool flags candidates for
human review).

Policy (all ``[TUNABLE]`` seeds in ``config.trade_plan``):
- **Entry**  — confirmation break of the range edge in the signal's direction.
- **Stop**   — structural invalidation (spring low / upthrust high, else the range edge) + buffer.
- **Target** — measured move: the range height projected from the breakout.
- **Size**   — account-risk %: risk a fixed % of a notional account across the stop distance.
- **Manage** — breakeven at +Nr, scale out at target, trail the runner (written rules, not executed).

Every derivation can ``return None`` (the planner *abstains*) rather than raise, so a
degenerate setup never breaks a run — same fail-soft contract as the rest of the pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import TradePlanConfig
from .strategies.base import Levels

_LONG = "accumulation"
_SHORT = "distribution"


@dataclass(frozen=True)
class TradePlan:
    """A suggested (never-executed) trade plan derived from a signal's structural levels.

    Prices are raw floats (the dashboard formats them). ``reward_risk`` is the reward:risk
    ratio to the measured-move target — surfaced because the chosen entry/stop interact to
    set it, and it's the single most honest number for judging a setup.
    """

    direction: str
    entry: float
    stop: float
    target: float
    risk_per_share: float
    reward_per_share: float
    reward_risk: float       # reward:risk ratio to target
    risk_amount: float       # $ risked = account_notional × risk_pct / 100
    size_shares: float       # position size that risks exactly risk_amount over the stop distance
    position_value: float    # size_shares × entry (notional exposure)
    management: list[str] = field(default_factory=list)


def plan_trade(direction: str, levels: Levels, cfg: TradePlanConfig) -> TradePlan | None:
    """Derive a ``TradePlan`` from a signal, or ``None`` when there's nothing plannable.

    Returns ``None`` (abstain, never crash) when: there's no tradeable direction, the range
    band is missing, or a degenerate level would make the stop distance zero (can't size).
    """
    if direction not in (_LONG, _SHORT):
        return None
    if levels.range_high is None or levels.range_low is None:
        return None
    range_height = levels.range_high - levels.range_low
    if range_height <= 0:
        return None  # degenerate / inverted band

    if direction == _LONG:
        entry = levels.range_high  # confirmation break above resistance
        invalidation = levels.spring_low if levels.spring_low is not None else levels.range_low
        stop = invalidation * (1.0 - cfg.stop_buffer_pct / 100.0)  # buffer below
        target = entry + range_height  # measured move up
    else:
        entry = levels.range_low  # confirmation break below support
        invalidation = levels.upthrust_high if levels.upthrust_high is not None else levels.range_high
        stop = invalidation * (1.0 + cfg.stop_buffer_pct / 100.0)  # buffer above
        target = entry - range_height  # measured move down

    risk_per_share = abs(entry - stop)
    if risk_per_share <= 0:
        return None  # entry and stop coincide -> can't size
    reward_per_share = abs(target - entry)
    risk_amount = cfg.account_notional * cfg.risk_pct / 100.0
    size_shares = risk_amount / risk_per_share

    return TradePlan(
        direction=direction,
        entry=entry,
        stop=stop,
        target=target,
        risk_per_share=risk_per_share,
        reward_per_share=reward_per_share,
        reward_risk=reward_per_share / risk_per_share,
        risk_amount=risk_amount,
        size_shares=size_shares,
        position_value=size_shares * entry,
        management=_management_playbook(target, cfg),
    )


def _management_playbook(target: float, cfg: TradePlanConfig) -> list[str]:
    """The management rules written into the plan — human-readable, NEVER auto-executed."""
    return [
        f"Move stop to breakeven once price reaches +{cfg.breakeven_at_r:g}R.",
        f"Scale out {cfg.scale_out_pct:g}% at the measured-move target ({target:.2f}).",
        f"Trail the remainder by {cfg.trail_atr_mult:g}×ATR.",
    ]
