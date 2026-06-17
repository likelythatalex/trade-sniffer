"""Tests for state.py — dedup transitions, persistence, MTF cross-read (SPEC §9A)."""
from __future__ import annotations

from pathlib import Path

from src.state import (
    TimeframeState,
    classify_transitions,
    load_state,
    mtf_direction,
    save_state,
)


def test_classify_transitions() -> None:
    transitions = classify_transitions(prior={"A", "B"}, current={"B", "C"})
    assert transitions == {"C": "new", "B": "continuing", "A": "failed"}


def test_classify_cold_start_all_new() -> None:
    assert classify_transitions(prior=set(), current={"A", "B"}) == {"A": "new", "B": "new"}


def test_classify_empty_current_all_failed() -> None:
    assert classify_transitions(prior={"A"}, current=set()) == {"A": "failed"}


def test_state_round_trips(tmp_path: Path) -> None:
    state = {
        "daily": TimeframeState(qualifying={"XOM": {"score": 72.0, "direction": "accumulation"}}, run_ts="t1"),
        "weekly": TimeframeState(qualifying={}, run_ts="t2"),
    }
    path = tmp_path / "state.json"
    save_state(path, state)
    loaded = load_state(path)
    assert loaded["daily"].qualifying == {"XOM": {"score": 72.0, "direction": "accumulation"}}
    assert loaded["weekly"].run_ts == "t2"


def test_load_missing_state_is_cold_start(tmp_path: Path) -> None:
    assert load_state(tmp_path / "nope.json") == {}


def test_mtf_direction_reads_other_timeframe() -> None:
    state = {
        "weekly": TimeframeState(
            qualifying={"XOM": {"score": 80.0, "direction": "accumulation"}}, run_ts="t"
        )
    }
    # A daily run reads the stored weekly result for the cross-read.
    assert mtf_direction(state, "weekly", "XOM") == "accumulation"
    assert mtf_direction(state, "weekly", "AAPL") is None  # not qualifying there
    assert mtf_direction(state, "daily", "XOM") is None  # no stored daily result (cold)
