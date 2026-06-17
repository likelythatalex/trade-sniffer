"""Prior-run state for notification dedup + the multi-timeframe cross-read (SPEC §9A).

Two consumers: notification dedup (§8.3) and MTF agreement (§7.3 — the running
timeframe reads the *other* timeframe's most recent stored result, never recomputes
it). File I/O is isolated here; the transition classification is a **pure function**
so it's unit-testable on its own.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# transition enum (SPEC §8.4): "invalidated" -> failed, "still-qualifying" -> continuing.
TRANSITIONS = ("new", "continuing", "failed", "none")


@dataclass
class TimeframeState:
    """Stored result for one timeframe, so the other timeframe can cross-read it.

    Attributes:
        qualifying: ticker -> {"score": float, "direction": str} for tickers that
            made the watchlist this run (direction enables the MTF same-direction check).
        run_ts: when this state was written.
    """

    qualifying: dict[str, dict[str, Any]]
    run_ts: str


def classify_transitions(prior: set[str], current: set[str]) -> dict[str, str]:
    """Pure: map each ticker to its transition given prior vs. current qualifiers.

    new = in current, not prior. failed = in prior, not current. continuing = both.
    Tickers in neither are simply absent (the caller logs those as ``none``).
    """
    transitions = {ticker: "new" for ticker in current - prior}
    transitions.update({ticker: "continuing" for ticker in current & prior})
    transitions.update({ticker: "failed" for ticker in prior - current})
    return transitions


def mtf_direction(state: dict[str, TimeframeState], other_timeframe: str, ticker: str) -> str | None:
    """The other timeframe's stored direction for ``ticker`` (for the MTF agreement
    bonus), or ``None`` if it didn't qualify there / there's no stored result (cold start)."""
    other = state.get(other_timeframe)
    if other is None:
        return None
    entry = other.qualifying.get(ticker)
    return entry["direction"] if entry else None


def load_state(path: Path) -> dict[str, TimeframeState]:
    """Load per-timeframe state (cold start -> empty dict)."""
    path = Path(path)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        timeframe: TimeframeState(qualifying=entry.get("qualifying", {}), run_ts=entry.get("run_ts", ""))
        for timeframe, entry in data.items()
    }


def save_state(path: Path, state: dict[str, TimeframeState]) -> None:
    """Persist per-timeframe state for the next run (idempotent overwrite)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {timeframe: {"run_ts": s.run_ts, "qualifying": s.qualifying} for timeframe, s in state.items()}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
