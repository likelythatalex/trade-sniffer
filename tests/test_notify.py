"""Tests for notify.py — message formatting + Discord send (mocked, no network)."""
from __future__ import annotations

import pytest

from src import notify
from src.notify import DiscordNotifier, _format_content, has_transitions, make_notifier


def test_format_content_lists_new_and_failed() -> None:
    summary = {
        "timeframe": "daily",
        "new": [{"ticker": "XOM", "direction": "accumulation", "score": 72.0}],
        "failed": ["KO"],
        "report_url": "https://example.com/r.html",
    }
    text = _format_content(summary)
    assert "daily" in text and "1 NEW" in text and "1 FAILED" in text
    assert "XOM" in text and "KO" in text
    assert "https://example.com/r.html" in text


def test_format_content_cold_start_is_condensed() -> None:
    summary = {"timeframe": "weekly", "cold_start": True,
               "new": [{"ticker": "XOM", "direction": "accumulation", "score": 80.0}]}
    text = _format_content(summary)
    assert "first run" in text and "XOM" in text


def test_has_transitions() -> None:
    assert has_transitions({"new": [{"ticker": "X"}], "failed": []}) is True
    assert has_transitions({"new": [], "failed": ["Y"]}) is True
    assert has_transitions({"new": [], "failed": []}) is False


def test_send_posts_payload_to_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return FakeResponse()

    monkeypatch.setattr(notify.requests, "post", fake_post)
    DiscordNotifier("https://discord/webhook").send(
        {"timeframe": "daily", "new": [{"ticker": "XOM", "direction": "accumulation", "score": 72.0}], "failed": []}
    )
    assert captured["url"] == "https://discord/webhook"
    assert "XOM" in captured["json"]["content"]


def test_send_failure_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a, **_k):
        raise RuntimeError("network down")

    monkeypatch.setattr(notify.requests, "post", boom)
    # Notification failure must never raise into the run (best-effort).
    DiscordNotifier("https://discord/webhook").send({"timeframe": "daily", "new": [], "failed": []})


def test_make_notifier_rejects_unknown_channel() -> None:
    assert isinstance(make_notifier("discord", "https://x"), DiscordNotifier)
    with pytest.raises(ValueError):
        make_notifier("telegram", "https://x")
