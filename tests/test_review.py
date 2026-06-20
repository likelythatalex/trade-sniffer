"""Tests for the agent reviewer (SPEC §8.5).

Hermetic: a StubReviewer stands in for the LLM (no network/spend). We assert the cost
controls (NEW-only, per-run cap, caching) and the plumbing (prompt building, verdict
parsing, attaching reviews to cards), not model quality.
"""
from __future__ import annotations

import dataclasses
from datetime import date
from pathlib import Path

from src import config as config_module
from src.review import (
    Reviewer,
    build_review_prompt,
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
                  "range_high": 110.0, "range_low": 100.0, "marker": None},
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


def test_build_review_prompt_includes_evidence() -> None:
    prompt = build_review_prompt(_card("XOM", score=82.0))
    assert "XOM" in prompt and "accumulation" in prompt and "82" in prompt
    assert "volume_behavior" in prompt and "spring at support" in prompt
    assert "100.0" in prompt and "110.0" in prompt  # range bounds


def test_reviews_cache_round_trip_and_tolerant(tmp_path: Path) -> None:
    path = tmp_path / "reviews.json"
    assert load_reviews(path) == {}  # missing -> empty
    save_reviews(path, {"daily:XOM": {"text": "x", "verdict": "mixed"}})
    assert load_reviews(path)["daily:XOM"]["verdict"] == "mixed"
    (tmp_path / "bad.json").write_text("not json", encoding="utf-8")
    assert load_reviews(tmp_path / "bad.json") == {}  # corrupt -> empty, no crash


# --- make_reviewer ------------------------------------------------------------


def test_make_reviewer_disabled_or_no_key_returns_none() -> None:
    disabled = dataclasses.replace(CONFIG.review, enabled=False)
    assert make_reviewer(disabled, "key") is None  # disabled even with a key
    assert make_reviewer(_review_config().review, None) is None  # enabled but no key


def test_make_reviewer_builds_when_enabled_with_key() -> None:
    reviewer = make_reviewer(_review_config().review, "secret-key")
    assert reviewer is not None


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
    path = tmp_path / "r.json"
    save_reviews(path, {"daily:AAA": {"text": "cached", "verdict": "mixed"}})
    cards = [_card("AAA")]
    stub = StubReviewer()
    review_candidates(cards, {"AAA": "new"}, "daily", _review_config(), date(2024, 6, 1), path, stub)
    assert stub.calls == 0  # already cached -> no LLM call
    assert cards[0]["review"]["text"] == "cached"  # cached review still shown


def test_disabled_reviewer_attaches_cached_but_generates_nothing(tmp_path: Path) -> None:
    # A continuing setup keeps its earlier review even when generation is off.
    path = tmp_path / "r.json"
    save_reviews(path, {"daily:AAA": {"text": "prior", "verdict": "aligned"}})
    cards = [_card("AAA")]
    review_candidates(cards, {"AAA": "continuing"}, "daily", CONFIG, date(2024, 6, 1), path, None)
    assert cards[0]["review"]["text"] == "prior"
