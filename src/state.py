"""Prior-run state for notification dedup + the multi-timeframe cross-read (SPEC §9A).

Two consumers: notification dedup (§8.3) and MTF agreement (§7.3, the running
timeframe reads the *other* timeframe's most recent stored result — never
recomputes it). File I/O is isolated to this module; the transition classification
is a **pure function** so it's unit-testable on its own.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# transition enum (SPEC §8.4): "invalidated" -> failed, "still-qualifying" -> continuing.
TRANSITIONS = ("new", "continuing", "failed", "none")


@dataclass
class TimeframeState:
    """Stored result for one timeframe, so the other timeframe can cross-read it."""

    qualifying: dict[str, float]  # ticker -> last composite score (>= threshold)
    run_ts: str


def classify_transitions(
    prior: set[str], current: set[str]
) -> dict[str, str]:
    """Pure: map each ticker to its transition given prior vs. current qualifiers.

    new: in current, not prior. failed: in prior, not current. continuing: in both.
    (Tickers in neither are simply absent — caller logs them as ``none``.)
    """
    raise NotImplementedError


def load_state(path: Path) -> dict[str, TimeframeState]:
    """Load per-timeframe state (cold start → empty)."""
    raise NotImplementedError


def save_state(path: Path, state: dict[str, TimeframeState]) -> None:
    """Persist per-timeframe state for the next run (idempotent)."""
    raise NotImplementedError
