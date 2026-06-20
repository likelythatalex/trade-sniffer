"""Agent reviewer — an objective, proactive due-diligence pass on flagged setups (SPEC §8.5).

This is NOT a chat window. It fires automatically when a setup *newly* flags, applies the
SAME skeptical rubric to every candidate (so it's a consistent second opinion, not an echo),
and emits a structured verdict baked into the dashboard. It reviews the *number* and the
evidence — it never gives trading advice (the tool flags candidates, it never trades).

Strategy-agnostic: it consumes the normalized card (direction, score, sub-scores, reason
tags, recent price action) — the same contract every strategy emits — so future strategies
are reviewed with no changes here.

Cost controls (public repo): off by default; NEW transitions only; a hard per-run cap; a
cheap model; bounded output; cached by ``timeframe:ticker`` so continuing setups and same-day
re-runs never re-spend. The LLM call is a plain REST request (no SDK dependency), and
everything is fail-soft — no key or a failed call simply omits the review.
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path
from typing import Any

from .config import Config, ReviewConfig

logger = logging.getLogger(__name__)

_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"
_VERDICTS = ("aligned", "mixed", "skeptical")

SYSTEM_PROMPT = (
    "You are a skeptical due-diligence reviewer for a Wyckoff accumulation/distribution "
    "scanner. You are given one flagged setup's structured evidence. Provide an objective "
    "second opinion for a human reviewer: weigh the setup against Wyckoff/VSA principles, and "
    "actively surface concerns, red flags, and places where the evidence is thin or "
    "conflicting. Apply the same scrutiny every time. Be concise and specific.\n\n"
    "IMPORTANT: Do NOT give trading advice, price targets, or buy/sell/hold recommendations — "
    "this tool flags candidates for human review and never trades. These are analyst notes.\n\n"
    "Output format:\n"
    "Verdict: <aligned|mixed|skeptical>   (your agreement with the flag)\n"
    "<2-4 sentence assessment>\n"
    "Concerns:\n"
    "- <1 to 3 short bullets>"
)


# --- Reviewer interface (pluggable, like notify.Notifier) ---------------------


class Reviewer(ABC):
    """Produces a review for a built prompt. Pure interface so it's easy to stub in tests."""

    @abstractmethod
    def review(self, prompt: str) -> dict[str, str]:
        """Return ``{"text": ..., "verdict": ...}`` for the given prompt."""


class AnthropicReviewer(Reviewer):
    """Calls the Anthropic Messages API over REST (no SDK dependency).

    The ``system_prompt`` + ``verdicts`` are injected so the same client serves both the
    signal due-diligence review (default) and the journal's post-trade reflection (§8A.2) —
    one HTTP client, two rubrics.
    """

    def __init__(
        self,
        model: str,
        api_key: str,
        max_tokens: int,
        system_prompt: str = SYSTEM_PROMPT,
        verdicts: tuple[str, ...] = _VERDICTS,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._max_tokens = max_tokens
        self._system_prompt = system_prompt
        self._verdicts = verdicts

    def review(self, prompt: str) -> dict[str, str]:
        import requests  # lazy: only needed when the reviewer actually runs

        response = requests.post(
            _ANTHROPIC_URL,
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": _ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            json={
                "model": self._model,
                "max_tokens": self._max_tokens,
                "system": self._system_prompt,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        response.raise_for_status()
        text = response.json()["content"][0]["text"].strip()
        return {"text": text, "verdict": parse_verdict(text, self._verdicts)}


def make_reviewer(config: ReviewConfig, api_key: str | None) -> Reviewer | None:
    """Build a reviewer, or ``None`` when disabled / no key (reviewer simply skips)."""
    if not config.enabled:
        return None
    if not api_key:
        logger.info("review skipped: %s not set", config.api_key_env)
        return None
    return AnthropicReviewer(config.model, api_key, config.max_tokens)


# --- Orchestration (the cost controls live here) ------------------------------


def review_candidates(
    cards: list[dict[str, Any]],
    transitions: dict[str, str],
    timeframe: str,
    config: Config,
    today: date,
    reviews_path: Path,
    reviewer: Reviewer | None = None,
) -> None:
    """Attach a ``review`` to each flagged card (in place), generating new ones only for
    NEW transitions, capped and cached. Continuing setups reuse their earlier review.

    The reviewer is injected (defaults to one built from config+env) so tests stay hermetic.
    Cache key is ``timeframe:ticker`` in ``reviews_path`` (carried on gh-pages).
    """
    import os

    rcfg = config.review
    cache = load_reviews(reviews_path)

    if reviewer is None:
        reviewer = make_reviewer(rcfg, os.environ.get(rcfg.api_key_env))

    if reviewer is not None:
        to_generate = _select_for_review(cards, transitions, cache, timeframe, rcfg)
        for card in to_generate:
            try:
                result = reviewer.review(build_review_prompt(card))
            except Exception:  # fail soft: a review failure never breaks the run
                logger.exception("review failed for %s", card["ticker"])
                continue
            cache[_key(timeframe, card["ticker"])] = {**result, "model": rcfg.model, "date": today.isoformat()}
        save_reviews(reviews_path, cache)

    # Attach for display: every flagged card shows its cached review (new or from a prior run).
    for card in cards:
        cached = cache.get(_key(timeframe, card["ticker"]))
        if cached:
            card["review"] = cached


def _select_for_review(
    cards: list[dict[str, Any]],
    transitions: dict[str, str],
    cache: dict[str, Any],
    timeframe: str,
    rcfg: ReviewConfig,
) -> list[dict[str, Any]]:
    """NEW (or, if only_new is false, any) flagged cards without a cached review, highest
    score first, capped at ``max_reviews_per_run``."""
    candidates = [
        card
        for card in cards
        if (not rcfg.only_new or transitions.get(card["ticker"]) == "new")
        and _key(timeframe, card["ticker"]) not in cache
    ]
    candidates.sort(key=lambda card: card.get("score", 0.0), reverse=True)
    return candidates[: rcfg.max_reviews_per_run]


def build_review_prompt(card: dict[str, Any]) -> str:
    """Compact, structured evidence for one setup (kept small to bound tokens)."""
    chart = card.get("chart", {})
    candles = chart.get("candles", [])
    recent = candles[-10:]
    sub = "; ".join(f"{name.split('.')[-1]}: {value:+.0f}" for name, value in card.get("sub_scores", {}).items())
    reasons = "; ".join(card.get("reasons", [])) or "(none)"
    bars = "\n".join(
        f"  {c['time']}: O{c['open']} H{c['high']} L{c['low']} C{c['close']}" for c in recent
    )
    last_close = recent[-1]["close"] if recent else "n/a"
    return (
        f"Setup: {card['ticker']} — {card['direction']} — conviction {card['score']:.0f}/100\n"
        f"Sub-scores (signed, + = accumulation): {sub or '(none)'}\n"
        f"Reason tags: {reasons}\n"
        f"Trading range: low {chart.get('range_low')} / high {chart.get('range_high')}; "
        f"last close {last_close}\n"
        f"Spring/upthrust marker: {chart.get('marker') or 'none'}\n"
        f"Recent bars (oldest→newest):\n{bars or '  (none)'}\n\n"
        "Review this setup per your rubric."
    )


def parse_verdict(text: str, verdicts: tuple[str, ...] = _VERDICTS) -> str:
    """Lenient: pull the verdict word from the first line; default 'n/a'. ``verdicts`` is the
    vocabulary to look for (signal review vs the post-trade reflection use different words)."""
    first = text.strip().splitlines()[0].lower() if text.strip() else ""
    return next((v for v in verdicts if v in first), "n/a")


# --- Cache I/O (tolerant, like state.py) --------------------------------------


def _key(timeframe: str, ticker: str) -> str:
    return f"{timeframe}:{ticker}"


def load_reviews(path: Path) -> dict[str, Any]:
    """Load the review cache; missing/empty/corrupt → empty (never crash a run)."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8").strip()
        data = json.loads(text) if text else {}
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save_reviews(path: Path, cache: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2), encoding="utf-8")
