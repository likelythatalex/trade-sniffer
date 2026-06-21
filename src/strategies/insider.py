"""Insider-transaction strategy — an INDEPENDENT, non-price signal (SPEC §6, §12).

The literal version of Wyckoff's premise: Wyckoff *infers* the "composite operator" (smart
money) from price/volume footprints; this reads the smart money's own **disclosed** Form 4
transactions. Footprint-inference + disclosed-fact agreeing is real corroboration — and the
signal is orthogonal to both price (Wyckoff/momentum) and media (sentiment).

Unlike news sentiment, EDGAR keeps Form 4s historically, so this signal **is backtestable**
(the no-lookahead cutoff uses each transaction's *filing* date). Ships at **weight 0** (logged
as ``insider_score``, inert) until calibration.

Scoring (relative, per the repo rule — a self-normalizing *ratio*, never an absolute dollar
threshold): net buy value over total, where sells are down-weighted because insider *selling*
is noisy (diversification, taxes, 10b5-1 plans) while *buying* is the higher-signal side.
Sparse by nature (insiders trade infrequently), so it abstains often — abstain (no data) is
kept distinct from neutral (data, no lean). Pure: transactions are fetched upstream.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

import pandas as pd

from ..insider_data import InsiderTxn
from .base import Strategy, StrategyContext, StrategyResult

DIRECTION_FLOOR = 10.0  # |signed| below this -> direction "none"
_OPEN_MARKET_BUY = "P"   # open-market purchase (the high-signal event)
_OPEN_MARKET_SELL = "S"  # open-market sale (down-weighted: often non-informational)


class InsiderStrategy(Strategy):
    """Net open-market insider buying vs selling → a signed, directional conviction score."""

    name = "insider"

    def evaluate(self, df: pd.DataFrame, context: StrategyContext) -> StrategyResult:
        params = context.params
        if context.insider_transactions is None:
            return _abstain()  # no data fetched -> abstain (distinct from neutral)

        in_window = _txns_in_window(context.insider_transactions, df, int(params["lookback_days"]))
        signed, breakdown = _score(in_window, float(params.get("sell_weight", 0.5)))
        if signed is None:
            return _abstain()

        if signed >= DIRECTION_FLOOR:
            direction = "accumulation"
        elif signed <= -DIRECTION_FLOOR:
            direction = "distribution"
        else:
            direction = "none"

        reasons = []
        if direction == "accumulation":
            reasons.append(f"net insider buying ({breakdown['n_buy_owners']} buyer(s))")
        elif direction == "distribution":
            reasons.append("net insider selling")

        return StrategyResult(
            direction=direction,
            score=abs(signed),
            reasons=reasons,
            metadata={"signed": signed, **breakdown},  # signed=None elsewhere = "no data"
        )


def _abstain() -> StrategyResult:
    return StrategyResult(
        direction="none", score=0.0,
        metadata={"signed": None, "n_buys": 0, "n_sells": 0, "n_buy_owners": 0},
    )


def _score(
    txns: list[InsiderTxn], sell_weight: float
) -> tuple[float | None, dict[str, Any]]:
    """Signed score in [-100, +100] from open-market buys/sells, or ``(None, …)`` to abstain.

    Relative + self-normalizing: ``(buy − w·sell) / (buy + w·sell)`` on transaction *value*
    (falling back to share count when price is absent), so it's comparable across stocks with
    no absolute dollar threshold. ``sell_weight`` (``w``) down-weights noisy insider selling.
    """
    buys = [t for t in txns if t.code == _OPEN_MARKET_BUY]
    sells = [t for t in txns if t.code == _OPEN_MARKET_SELL]
    buy_w = sum(_weight(t) for t in buys)
    sell_w = sum(_weight(t) for t in sells) * sell_weight
    breakdown = {"n_buys": len(buys), "n_sells": len(sells), "n_buy_owners": len({t.owner for t in buys})}

    total = buy_w + sell_w
    if total <= 0:  # no open-market buys or sells in the window -> abstain
        return None, breakdown
    ratio = (buy_w - sell_w) / total
    return max(-1.0, min(1.0, ratio)) * 100.0, breakdown


def _weight(txn: InsiderTxn) -> float:
    """Transaction magnitude: dollar value when known, else share count (always > 0)."""
    return txn.value if txn.value > 0 else txn.shares


def _txns_in_window(
    txns: list[InsiderTxn], df: pd.DataFrame, lookback_days: int
) -> list[InsiderTxn]:
    """Transactions whose **filing date** is within ``[bar_close − lookback_days, bar_close]``.

    Filing date (public-availability) is the no-lookahead key — never the earlier transaction
    date. If the index carries no date (e.g. an integer test index), all are kept."""
    last = df.index[-1] if len(df.index) else None
    cutoff = last.date() if hasattr(last, "date") else None
    if cutoff is None:
        return list(txns)
    earliest = cutoff - timedelta(days=lookback_days)
    return [t for t in txns if earliest <= t.filing_date <= cutoff]
