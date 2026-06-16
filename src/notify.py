"""Notifications (SPEC §8.3). Discord-only in v1, channel-pluggable by design.

Fires on **state transitions** (NEW / FAILED), not the full list, to avoid noise;
still-qualifying setups are not re-notified. Cold start sends a condensed summary
instead of a per-ticker flood, and an empty run is suppressed entirely
(``notify.suppress_empty``). The webhook URL comes from an env var, never committed.

``Notifier`` is the seam a future Telegram channel plugs into without touching
callers — kept minimal (one ``send`` method), per interface-segregation.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Notifier(ABC):
    """Channel interface. v1 ships only ``DiscordNotifier``."""

    @abstractmethod
    def send(self, summary: dict[str, Any]) -> None:
        """Push a run summary (counts of NEW/FAILED, top NEW tickers, report link)."""
        raise NotImplementedError


class DiscordNotifier(Notifier):
    """Posts to a Discord webhook (URL from env, per ``notify.webhook_url_env``)."""

    def __init__(self, webhook_url: str) -> None:
        self._webhook_url = webhook_url

    def send(self, summary: dict[str, Any]) -> None:
        raise NotImplementedError
