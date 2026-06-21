"""Strategy registry — maps a config name to a Strategy class (SPEC §6).

``scanner.py`` discovers strategies from the ``strategies:`` block in
``config.yaml`` rather than hardcoding them, so adding a strategy is "new file +
config line". The mapping is intentionally explicit (a plain dict): for a
single-maintainer tool that is easier to read than import-time auto-discovery.
"""
from __future__ import annotations

from .base import Strategy
from .momentum import MomentumStrategy
from .news_sentiment import NewsSentimentStrategy
from .wyckoff import WyckoffStrategy

# name -> class. The key must match a key under ``strategies:`` in config.yaml.
_REGISTRY: dict[str, type[Strategy]] = {
    "wyckoff": WyckoffStrategy,
    "momentum": MomentumStrategy,
    "news_sentiment": NewsSentimentStrategy,
}


def get_strategy(name: str) -> Strategy:
    """Instantiate the strategy registered under ``name``.

    Raises:
        KeyError: if no strategy is registered under that name. We fail loud — a
            typo in config.yaml should surface, not silently disable a strategy.
    """
    try:
        strategy_cls = _REGISTRY[name]
    except KeyError as exc:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise KeyError(f"Unknown strategy '{name}'. Known: {known}.") from exc
    return strategy_cls()
