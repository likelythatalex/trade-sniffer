"""Market context — a once-per-run, market-wide reading (SPEC §12).

Distinct from the per-ticker `Strategy` seam: strategies ask "is THIS stock setting up?";
this asks "what's the environment?" — computed once per run from closed bars, shared across
all tickers. v1 (Phase 1) is **regime** (SPY vs its trend MA) + **breadth** (% of the scanned
universe above their own MA). It is **displayed + logged only** — not yet applied to scores,
because a market-wide reading can't be calibrated the per-ticker way (annotate first, scale
later). Pure: series are fetched upstream and passed in. Macro/intermarket inputs are FUTURE.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# A market is "healthy" when at least this share of names participate (are above their MA).
# [TUNABLE] seed; combined with the SPY regime to label risk-on / risk-off / neutral.
BREADTH_HEALTHY_PCT = 50.0


@dataclass(frozen=True)
class MarketContext:
    """The market-wide reading for one run/timeframe.

    Attributes:
        regime: ``"risk-on"`` | ``"risk-off"`` | ``"neutral"`` | ``"unknown"`` (unknown when
            SPY history is too short to judge).
        spy_above_ma: is SPY's last close at/above its ``ma_window`` MA? ``None`` if unknown.
        spy_distance_pct: SPY's % distance from that MA (+ above / − below). ``None`` if unknown.
        breadth_pct: % of measurable universe names above their own MA. ``None`` if none measurable.
        n_breadth: how many names had enough history to be counted (the breadth denominator).
        ma_window: the trend MA window used.
    """

    regime: str
    spy_above_ma: bool | None
    spy_distance_pct: float | None
    breadth_pct: float | None
    n_breadth: int
    ma_window: int


def compute_market_context(
    spy_close: pd.Series | None,
    universe_closes: list[pd.Series],
    ma_window: int,
) -> MarketContext:
    """Build the once-per-run market reading from closed-bar closes.

    ``spy_close`` drives the regime (last close vs its trailing ``ma_window`` mean);
    ``universe_closes`` (one close series per fetched ticker) drives breadth — each name that
    has at least ``ma_window`` bars counts as participating if its last close ≥ its own MA.
    Names with too little history are excluded from the breadth denominator (graceful, no
    coercion). The regime label blends SPY position with breadth; it's ``"unknown"`` when SPY
    can't be judged.
    """
    spy_above, spy_distance = _above_ma(spy_close, ma_window)
    breadth_pct, n_breadth = _breadth(universe_closes, ma_window)
    regime = _regime_label(spy_above, breadth_pct)
    return MarketContext(
        regime=regime,
        spy_above_ma=spy_above,
        spy_distance_pct=spy_distance,
        breadth_pct=breadth_pct,
        n_breadth=n_breadth,
        ma_window=ma_window,
    )


def _above_ma(close: pd.Series | None, ma_window: int) -> tuple[bool | None, float | None]:
    """``(is_last_close_>=_MA, pct_distance_from_MA)`` or ``(None, None)`` if too short/degenerate."""
    if close is None or len(close) < ma_window or ma_window <= 0:
        return None, None
    ma = float(close.iloc[-ma_window:].mean())
    last = float(close.iloc[-1])
    if not (ma > 0) or pd.isna(ma) or pd.isna(last):
        return None, None
    return last >= ma, (last - ma) / ma * 100.0


def _breadth(universe_closes: list[pd.Series], ma_window: int) -> tuple[float | None, int]:
    """% of names whose last close ≥ their own MA, over names with enough history."""
    measurable = [c for c in universe_closes if len(c) >= ma_window and ma_window > 0]
    above = 0
    counted = 0
    for close in measurable:
        result, _ = _above_ma(close, ma_window)
        if result is None:  # degenerate (e.g. flat/NaN) — don't count in the denominator
            continue
        counted += 1
        above += int(result)
    if counted == 0:
        return None, 0
    return above / counted * 100.0, counted


def _regime_label(spy_above: bool | None, breadth_pct: float | None) -> str:
    """Blend SPY position with breadth into a regime label.

    risk-on = SPY above its MA *and* healthy participation; risk-off = SPY below *and* weak
    participation; anything mixed = neutral. Unknown when SPY itself can't be judged."""
    if spy_above is None:
        return "unknown"
    healthy = breadth_pct is not None and breadth_pct >= BREADTH_HEALTHY_PCT
    weak = breadth_pct is not None and breadth_pct < BREADTH_HEALTHY_PCT
    if spy_above and healthy:
        return "risk-on"
    if (not spy_above) and weak:
        return "risk-off"
    return "neutral"
