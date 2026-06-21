"""News-sentiment strategy — an INDEPENDENT, non-price signal (SPEC §6, §12).

Wyckoff and momentum both read price; this reads *headlines*, so it's genuinely orthogonal
information — the most valuable kind for confirmation stacking. Like momentum it ships at
**weight 0** (logged as ``news_sentiment_score``, inert in the composite) so its forward
predictive value can be measured before calibration decides whether/how to weight it.

It is **forward-only**: free historical news doesn't exist and the replay backtester can't
reconstruct "what was the news on a past date", so the live ``signals.csv` is the *only*
dataset that can ever validate this signal (hence: log it now, judge it later).

Pure: headlines are fetched upstream and handed in via ``context.headlines``; the scorer is a
pluggable engine from ``sentiment.py``. The strategy adds the **as-of (no-lookahead) cutoff**
and the directional mapping. Named ``news_sentiment`` to reserve ``social_sentiment`` for a
future, independent crowd-sentiment strategy.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

import pandas as pd

from ..sentiment import aggregate_sentiment, get_scorer
from .base import Strategy, StrategyContext, StrategyResult

DIRECTION_FLOOR = 10.0  # |signed| below this -> direction "none"


class NewsSentimentStrategy(Strategy):
    """Recent-headline polarity → a signed, directional conviction score."""

    name = "news_sentiment"

    def evaluate(self, df: pd.DataFrame, context: StrategyContext) -> StrategyResult:
        params = context.params
        # Abstain (no data) when headlines weren't fetched — distinct from neutral sentiment.
        if context.headlines is None:
            return _abstain()

        in_window = _headlines_in_window(
            context.headlines, df, int(params["lookback_days"])
        )
        scorer = get_scorer(str(params.get("scorer", "vader")))
        signed = aggregate_sentiment([title for _, title in in_window], scorer)

        if signed is None:  # had a feed, but nothing within the as-of window -> abstain
            return _abstain()

        if signed >= DIRECTION_FLOOR:
            direction = "accumulation"
        elif signed <= -DIRECTION_FLOOR:
            direction = "distribution"
        else:
            direction = "none"

        reasons = []
        if direction == "accumulation":
            reasons.append(f"positive news sentiment ({len(in_window)} headlines)")
        elif direction == "distribution":
            reasons.append(f"negative news sentiment ({len(in_window)} headlines)")

        return StrategyResult(
            direction=direction,
            score=abs(signed),
            reasons=reasons,
            # signed (for the correlation study) + count; signed=None elsewhere means "no data".
            metadata={"signed": signed, "n_headlines": len(in_window)},
        )


def _abstain() -> StrategyResult:
    """No usable sentiment data → finite zero, direction none, signed=None (logged blank)."""
    return StrategyResult(direction="none", score=0.0, metadata={"signed": None, "n_headlines": 0})


def _headlines_in_window(
    headlines: list[tuple[Any, str]], df: pd.DataFrame, lookback_days: int
) -> list[tuple[Any, str]]:
    """Headlines within ``[bar_close - lookback_days, bar_close]`` by calendar date.

    The upper bound is the **no-lookahead** guard (SPEC: only data available at the evaluated
    bar's close). The cutoff is the last bar's date; comparing dates side-steps tz mismatches
    between the (UTC) publish time and a tz-naive daily index. If the index carries no date
    (e.g. an integer test index), the window can't be applied and all headlines are kept."""
    last = df.index[-1] if len(df.index) else None
    cutoff = last.date() if hasattr(last, "date") else None
    if cutoff is None:
        return list(headlines)
    earliest = cutoff - timedelta(days=lookback_days)
    return [
        (published, title)
        for published, title in headlines
        if hasattr(published, "date") and earliest <= published.date() <= cutoff
    ]
