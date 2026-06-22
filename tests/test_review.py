"""Tests for the agent reviewer (SPEC §8.5).

Hermetic: a StubReviewer stands in for the LLM (no network/spend). We assert the cost
controls (NEW-only, per-run cap, caching) and the plumbing (prompt building, verdict
parsing, attaching reviews to cards), not model quality.
"""
from __future__ import annotations

import dataclasses
from datetime import date
from pathlib import Path

import pytest

from src import config as config_module
from src.review import (
    AnthropicReviewer,
    OllamaReviewer,
    OpenAICompatibleReviewer,
    Reviewer,
    _post_with_retries,
    build_provider,
    build_review_prompt,
    build_reviewer,
    load_reviews,
    make_reviewer,
    parse_verdict,
    review_candidates,
    save_reviews,
)

CONFIG = config_module.load_config(Path("config.yaml"))


class StubReviewer(Reviewer):
    """Counts calls so we can assert the cost controls fire."""

    def __init__(self) -> None:
        self.calls = 0

    def review(self, prompt: str) -> dict[str, str]:
        self.calls += 1
        return {"text": "Verdict: aligned\nLooks fine.", "verdict": "aligned"}


def _card(ticker: str, score: float = 80.0, direction: str = "accumulation") -> dict:
    return {
        "ticker": ticker, "direction": direction, "score": score,
        "sub_scores": {"wyckoff.volume_behavior": 70.0}, "reasons": ["spring at support"],
        "chart": {"candles": [{"time": "2024-05-31", "open": 1, "high": 2, "low": 0.5, "close": 1.5}],
                  "range_high": 110.0, "range_low": 100.0, "markers": []},
    }


def _review_config(**overrides) -> config_module.Config:
    rcfg = dataclasses.replace(CONFIG.review, enabled=True, **overrides)
    return dataclasses.replace(CONFIG, review=rcfg)


# --- pure helpers -------------------------------------------------------------


def test_parse_verdict() -> None:
    assert parse_verdict("Verdict: skeptical\n...") == "skeptical"
    assert parse_verdict("Verdict: ALIGNED") == "aligned"
    assert parse_verdict("no verdict line") == "n/a"
    assert parse_verdict("") == "n/a"


def test_parse_verdict_custom_vocabulary() -> None:
    # The post-trade reflection uses a different vocabulary (good/mixed/poor).
    vocab = ("good", "mixed", "poor")
    assert parse_verdict("Process: good\n...", vocab) == "good"
    assert parse_verdict("Process: POOR", vocab) == "poor"
    assert parse_verdict("Verdict: aligned", vocab) == "n/a"  # signal word not in this vocab


def test_anthropic_reviewer_injects_prompt_and_verdicts() -> None:
    # Same HTTP client, two rubrics: the system prompt + verdict vocab are injectable.
    r = AnthropicReviewer("m", "key", 100, system_prompt="REFLECT", verdicts=("good", "poor"))
    assert r._system_prompt == "REFLECT" and r._verdicts == ("good", "poor")
    default = AnthropicReviewer("m", "key", 100)
    assert default._verdicts == ("aligned", "mixed", "skeptical")  # signal default preserved


def test_build_review_prompt_includes_evidence() -> None:
    prompt = build_review_prompt(_card("XOM", score=82.0))
    assert "XOM" in prompt and "accumulation" in prompt and "82" in prompt
    assert "volume_behavior" in prompt and "spring at support" in prompt
    assert "100.0" in prompt and "110.0" in prompt  # range bounds
    assert "first time flagged" in prompt  # no prior history -> stated explicitly


def test_build_review_prompt_includes_episode_history() -> None:
    card = _card("XOM")
    card["episode_history"] = "Flagged once before on this timeframe. Most recent prior episode: ..."
    prompt = build_review_prompt(card)
    assert "Prior episode history: Flagged once before" in prompt


def test_reviews_cache_round_trip_and_tolerant(tmp_path: Path) -> None:
    path = tmp_path / "reviews.json"
    assert load_reviews(path) == {}  # missing -> empty
    save_reviews(path, {"daily:XOM": {"text": "x", "verdict": "mixed"}})
    assert load_reviews(path)["daily:XOM"]["verdict"] == "mixed"
    (tmp_path / "bad.json").write_text("not json", encoding="utf-8")
    assert load_reviews(tmp_path / "bad.json") == {}  # corrupt -> empty, no crash


# --- make_reviewer / build_reviewer -------------------------------------------


def test_make_reviewer_disabled_returns_none(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")  # even with a key, disabled -> None
    assert make_reviewer(dataclasses.replace(CONFIG.review, enabled=False)) is None


def test_make_reviewer_anthropic_needs_key(monkeypatch) -> None:
    monkeypatch.delenv("REVIEW_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert make_reviewer(_review_config().review) is None  # enabled, anthropic, no key


def test_make_reviewer_builds_anthropic_with_key(monkeypatch) -> None:
    monkeypatch.delenv("REVIEW_PROVIDER", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    assert isinstance(make_reviewer(_review_config().review), AnthropicReviewer)


def test_build_reviewer_ollama_needs_no_key(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("REVIEW_PROVIDER", raising=False)
    rcfg = dataclasses.replace(CONFIG.review, provider="ollama")
    assert isinstance(build_reviewer(rcfg), OllamaReviewer)  # local: no key required


def test_review_env_overrides_flip_provider_and_model(monkeypatch) -> None:
    # One committed (anthropic) config; env flips local runs to Ollama (the hybrid setup).
    monkeypatch.setenv("REVIEW_PROVIDER", "ollama")
    monkeypatch.setenv("REVIEW_MODEL", "qwen2.5:7b")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://gpu:11434")
    reviewer = build_reviewer(CONFIG.review)  # committed provider is anthropic
    assert isinstance(reviewer, OllamaReviewer)
    assert reviewer._model == "qwen2.5:7b" and reviewer._base_url == "http://gpu:11434"


def test_ollama_reviewer_calls_chat_endpoint_and_parses(monkeypatch) -> None:
    import requests

    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self) -> None: ...
        def json(self) -> dict:
            return {"message": {"content": "Verdict: aligned\nLooks fine."}}

    def fake_post(url, json=None, timeout=None):  # noqa: A002 - mirrors requests.post
        captured["url"], captured["json"] = url, json
        return FakeResponse()

    monkeypatch.setattr(requests, "post", fake_post)
    out = OllamaReviewer("qwen2.5:7b", "http://localhost:11434/", 200).review("evidence")
    assert out["text"].startswith("Verdict: aligned") and out["verdict"] == "aligned"
    assert captured["url"] == "http://localhost:11434/api/chat"  # base_url normalized + path
    assert captured["json"]["model"] == "qwen2.5:7b"
    assert captured["json"]["messages"][0]["role"] == "system"
    assert captured["json"]["stream"] is False


# --- DeepSeek / OpenAI-compatible provider ------------------------------------


class _FakeResponse:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self.status_code = status
        self._payload = payload

    def raise_for_status(self) -> None: ...
    def json(self) -> dict:
        return self._payload


def test_openai_compatible_reviewer_calls_chat_completions(monkeypatch) -> None:
    import requests

    captured: dict = {}

    def fake_post(url, headers=None, json=None, timeout=None):  # noqa: A002 - mirrors requests.post
        captured.update(url=url, headers=headers, json=json, timeout=timeout)
        return _FakeResponse({"choices": [{"message": {"content": "Verdict: aligned\nLooks fine."}}]})

    monkeypatch.setattr(requests, "post", fake_post)
    out = OpenAICompatibleReviewer("deepseek-v4-flash", "sk-x", "https://api.deepseek.com/", 200).review("evidence")

    assert out["text"].startswith("Verdict: aligned") and out["verdict"] == "aligned"
    assert captured["url"] == "https://api.deepseek.com/chat/completions"  # trailing / normalized + path
    assert captured["headers"]["Authorization"] == "Bearer sk-x"
    assert captured["json"]["model"] == "deepseek-v4-flash"
    assert captured["json"]["messages"][0]["role"] == "system"


def test_post_with_retries_recovers_then_succeeds(monkeypatch) -> None:
    import time

    import requests

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def flaky(url, headers=None, json=None, timeout=None):  # noqa: A002
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.RequestException("transient")
        return _FakeResponse({"ok": True})

    monkeypatch.setattr(requests, "post", flaky)
    resp = _post_with_retries("u", headers={}, payload={}, timeout=1, retries=2)
    assert calls["n"] == 2 and resp.json() == {"ok": True}  # retried once, then succeeded


def test_post_with_retries_raises_after_exhaustion(monkeypatch) -> None:
    import time

    import requests

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(requests, "post", lambda *a, **k: (_ for _ in ()).throw(requests.RequestException("down")))
    with pytest.raises(requests.RequestException):
        _post_with_retries("u", headers={}, payload={}, timeout=1, retries=1)


def test_build_provider_deepseek(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk")
    reviewer = build_provider("deepseek", "deepseek-v4-flash", "DEEPSEEK_API_KEY", "https://api.deepseek.com", 1000)
    assert isinstance(reviewer, OpenAICompatibleReviewer) and reviewer._base_url == "https://api.deepseek.com"


def test_build_provider_deepseek_no_key_is_none(monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert build_provider("deepseek", "m", "DEEPSEEK_API_KEY", "https://api.deepseek.com", 100) is None


def test_build_provider_unknown_is_none() -> None:
    assert build_provider("gpt5-imaginary", "m", "X", "u", 100) is None


def test_build_reviewer_deepseek_via_env(monkeypatch) -> None:
    # Flip the in-pipeline reviewer to DeepSeek by env; key comes from the configured api_key_env.
    monkeypatch.setenv("REVIEW_PROVIDER", "deepseek")
    monkeypatch.setenv("REVIEW_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv(CONFIG.review.api_key_env, "sk")  # default api_key_env is ANTHROPIC_API_KEY
    reviewer = build_reviewer(CONFIG.review)
    assert isinstance(reviewer, OpenAICompatibleReviewer)
    assert reviewer._base_url == "https://api.deepseek.com"  # REVIEW_BASE_URL plumbed through


# --- review_candidates: the cost controls -------------------------------------


def test_reviews_only_new_transitions(tmp_path: Path) -> None:
    cards = [_card("AAA"), _card("BBB")]
    transitions = {"AAA": "new", "BBB": "continuing"}
    stub = StubReviewer()
    review_candidates(cards, transitions, "daily", _review_config(), date(2024, 6, 1), tmp_path / "r.json", stub)
    assert stub.calls == 1  # only the NEW one
    assert "review" in cards[0] and "review" not in cards[1]


def test_reviews_capped_per_run(tmp_path: Path) -> None:
    cards = [_card(f"T{i}", score=float(i)) for i in range(7)]
    transitions = {c["ticker"]: "new" for c in cards}
    stub = StubReviewer()
    cfg = _review_config(max_reviews_per_run=3)
    review_candidates(cards, transitions, "daily", cfg, date(2024, 6, 1), tmp_path / "r.json", stub)
    assert stub.calls == 3  # hard cap honored
    reviewed = [c for c in cards if "review" in c]
    assert {c["ticker"] for c in reviewed} == {"T6", "T5", "T4"}  # top-N by score


def test_reviews_use_cache_and_dont_respend(tmp_path: Path) -> None:
    # Same-day re-run: a review cached TODAY is not regenerated (idempotent, no re-spend).
    path = tmp_path / "r.json"
    today = date(2024, 6, 1)
    save_reviews(path, {"daily:AAA": {"text": "cached", "verdict": "mixed", "date": today.isoformat()}})
    cards = [_card("AAA")]
    stub = StubReviewer()
    review_candidates(cards, {"AAA": "new"}, "daily", _review_config(), today, path, stub)
    assert stub.calls == 0  # already reviewed today -> no LLM call
    assert cards[0]["review"]["text"] == "cached"  # cached review still shown


def test_reflag_regenerates_stale_review(tmp_path: Path) -> None:
    # A re-flagged setup whose cached review is from a PRIOR episode (earlier day) is stale —
    # regenerate it (so it's reviewed against its history), unlike a same-day re-run.
    path = tmp_path / "r.json"
    save_reviews(path, {"daily:AAA": {"text": "old episode", "verdict": "aligned", "date": "2024-05-01"}})
    cards = [_card("AAA")]
    cards[0]["episode_history"] = "Flagged once before on this timeframe."
    stub = StubReviewer()
    review_candidates(cards, {"AAA": "new"}, "daily", _review_config(), date(2024, 6, 1), path, stub)
    assert stub.calls == 1  # stale prior-episode review -> regenerated
    assert cards[0]["review"]["text"] == "Verdict: aligned\nLooks fine."  # fresh review shown


def test_disabled_reviewer_attaches_cached_but_generates_nothing(tmp_path: Path) -> None:
    # A continuing setup keeps its earlier review even when generation is off.
    path = tmp_path / "r.json"
    save_reviews(path, {"daily:AAA": {"text": "prior", "verdict": "aligned"}})
    cards = [_card("AAA")]
    review_candidates(cards, {"AAA": "continuing"}, "daily", CONFIG, date(2024, 6, 1), path, None)
    assert cards[0]["review"]["text"] == "prior"
