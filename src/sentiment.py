"""Sentiment scoring — pure text → signed score, pluggable engine (SPEC §6, §12).

Mirrors the ``Reviewer`` seam: a small ``SentimentScorer`` interface with a default
deterministic implementation (VADER), so a future engine (a local Ollama LLM, FinBERT, …)
drops in without touching the strategy. The strategy stays pure — it calls a scorer here,
never does I/O or model loading itself.

**v1 caveat (be honest):** VADER is a social-media-tuned lexicon, *not* a finance model. It
is a deliberate "prove the pipeline + data coverage first" choice; the score is a
calibration seed (`news_sentiment_score`, logged at weight 0), not a trusted signal. Swap
the engine once accrued data shows whether headline sentiment carries any forward edge.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from functools import lru_cache

# A mean compound of this magnitude maps to a full ±100 reading. [TUNABLE] calibration seed —
# headlines rarely average a |compound| near 1, so a sub-1 full-scale keeps moderate
# sentiment from collapsing to ~0. Tuned later against signals.csv.
SENTIMENT_FULL_SCALE = 0.5


class SentimentScorer(ABC):
    """Scores one piece of text to a polarity in [-1, +1] (+ = positive/bullish)."""

    @abstractmethod
    def score(self, text: str) -> float:
        """Return a polarity in [-1, +1] for ``text``."""
        raise NotImplementedError


class VaderScorer(SentimentScorer):
    """VADER lexicon scorer. The analyzer loads its lexicon once per instance (cheap, no
    I/O after import); reuse one instance per run via ``get_scorer``."""

    def __init__(self) -> None:
        # Imported lazily so the dependency is only needed when the scorer is actually used.
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

        self._analyzer = SentimentIntensityAnalyzer()

    def score(self, text: str) -> float:
        return float(self._analyzer.polarity_scores(text)["compound"])


@lru_cache(maxsize=None)
def get_scorer(name: str) -> SentimentScorer:
    """Return the scorer registered under ``name`` (cached, so the lexicon loads once).

    Raises:
        ValueError: on an unknown engine name — fail loud rather than silently disable.
    """
    if name == "vader":
        return VaderScorer()
    raise ValueError(f"Unknown sentiment scorer '{name}'. Known: vader.")


def aggregate_sentiment(headlines: list[str], scorer: SentimentScorer) -> float | None:
    """Aggregate headline polarities into a signed score in [-100, +100], or ``None``.

    ``None`` (abstain) when there are no headlines — distinct from ``0.0`` (headlines that
    average neutral). The caller must preserve that distinction: abstain means "no data",
    neutral means "data, no lean". The aggregate is the mean compound, scaled by
    ``SENTIMENT_FULL_SCALE`` and clipped to ±100 (equal-weight; recency weighting is FUTURE).
    """
    if not headlines:
        return None
    mean_compound = sum(scorer.score(h) for h in headlines) / len(headlines)
    scaled = max(-1.0, min(1.0, mean_compound / SENTIMENT_FULL_SCALE))
    return scaled * 100.0
