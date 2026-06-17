"""Notifications (SPEC §8.3). Discord-only in v1, channel-pluggable by design.

Fires on **state transitions** (NEW / FAILED), not the full list, to avoid noise;
still-qualifying setups are not re-notified. Cold start sends a condensed summary
instead of a per-ticker flood, and an empty run is suppressed by the caller
(``notify.suppress_empty``). The webhook URL comes from an env var, never committed.

``Notifier`` is the seam a future Telegram channel plugs into without touching
callers — kept minimal (one ``send`` method), per interface-segregation. Sending is
best-effort: a notification failure is logged, never raised into the run.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

import requests

logger = logging.getLogger(__name__)

_MAX_LISTED = 5  # cap NEW tickers listed in a message


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
        payload = {"content": _format_content(summary)}
        try:
            response = requests.post(self._webhook_url, json=payload, timeout=10)
            response.raise_for_status()
        except Exception:  # best-effort: never let a notify failure kill the run
            logger.warning("Discord notification failed", exc_info=True)


def make_notifier(channel: str, webhook_url: str) -> Notifier:
    """Build the configured notifier. v1 supports only ``discord``."""
    if channel == "discord":
        return DiscordNotifier(webhook_url)
    raise ValueError(f"Unsupported notification channel '{channel}' (v1 supports 'discord').")


def has_transitions(summary: dict[str, Any]) -> bool:
    """True if the run produced any NEW or FAILED transition (for suppress_empty)."""
    return bool(summary.get("new") or summary.get("failed"))


def _format_content(summary: dict[str, Any]) -> str:
    """Build the Discord message text from a run summary (pure; testable)."""
    timeframe = summary.get("timeframe", "?")
    report_url = summary.get("report_url")
    link = f"\n{report_url}" if report_url else ""
    new = summary.get("new", [])
    failed = summary.get("failed", [])

    if summary.get("cold_start"):
        top = ", ".join(_fmt_ticker(item) for item in new[:_MAX_LISTED]) or "none"
        return f"**Wyckoff {timeframe}** — first run: {len(new)} qualifying.\nTop: {top}{link}"

    lines = [f"**Wyckoff {timeframe}** — {len(new)} NEW, {len(failed)} FAILED."]
    if new:
        lines.append("NEW: " + ", ".join(_fmt_ticker(item) for item in new[:_MAX_LISTED]))
    if failed:
        lines.append("FAILED: " + ", ".join(failed[:10]))
    return "\n".join(lines) + link


def _fmt_ticker(item: dict[str, Any]) -> str:
    return f"{item['ticker']} ({item['direction'][:3]}, {item['score']:.0f})"
