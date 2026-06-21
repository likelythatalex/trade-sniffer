"""Tests for episodes.py — reconstructing transition/episode history from signals.csv rows.

Rows mimic what ``report.read_signals`` yields (csv.DictReader → all values are strings),
so the reconstruction is exercised on realistic string-typed input.
"""
from __future__ import annotations

from src.episodes import (
    Episode,
    format_episode_history,
    prior_episodes,
    reconstruct_episodes,
)

# A five-run daily timeline (ISO strings sort chronologically).
RUNS = [f"2026-01-0{i}T22:00:00+00:00" for i in range(1, 6)]


def _row(run_ts, ticker, made, score=50.0, direction="accumulation", tf="daily"):
    return {
        "run_ts": run_ts, "ticker": ticker, "timeframe": tf,
        "made_watchlist": str(made), "composite_score": str(score), "direction": direction,
    }


def _aaa_rows():
    # AAA qualifies runs 1-3 (one episode, peaks 75), drops off run 4, re-flags run 5.
    return [
        _row(RUNS[0], "AAA", True, 60.0),
        _row(RUNS[1], "AAA", True, 75.0),
        _row(RUNS[2], "AAA", True, 70.0),
        _row(RUNS[3], "AAA", False, 40.0),
        _row(RUNS[4], "AAA", True, 55.0),
    ]


def test_reconstructs_two_episodes_with_a_gap() -> None:
    episodes = reconstruct_episodes(_aaa_rows(), "AAA", "daily")
    assert len(episodes) == 2

    first, second = episodes
    assert first.n_runs == 3 and first.peak_score == 75.0
    assert first.start_ts == RUNS[0] and first.end_ts == RUNS[2]
    assert first.ongoing is False            # ended before the latest run
    assert second.n_runs == 1 and second.ongoing is True  # reaches the latest run


def test_prior_episodes_excludes_the_ongoing_one() -> None:
    episodes = reconstruct_episodes(_aaa_rows(), "AAA", "daily")
    prior = prior_episodes(episodes)
    assert len(prior) == 1 and prior[0].peak_score == 75.0  # only the ended episode


def test_format_episode_history_summarizes_prior() -> None:
    text = format_episode_history(reconstruct_episodes(_aaa_rows(), "AAA", "daily"))
    assert text is not None
    assert "once" in text and "75/100" in text and "3 run" in text
    assert "2026-01-03" in text  # date the prior episode was last seen


def test_no_history_for_first_time_flag() -> None:
    # A single ongoing episode (qualified the last two runs, never invalidated) has no prior.
    rows = [_row(RUNS[3], "BBB", True), _row(RUNS[4], "BBB", True)]
    episodes = reconstruct_episodes(rows, "BBB", "daily")
    assert len(episodes) == 1 and episodes[0].ongoing is True
    assert prior_episodes(episodes) == []
    assert format_episode_history(episodes) is None


def test_counts_multiple_prior_episodes() -> None:
    # Qualify, gap, qualify, gap, qualify-and-ongoing -> two prior episodes.
    rows = [
        _row(RUNS[0], "CCC", True), _row(RUNS[1], "CCC", False),
        _row(RUNS[2], "CCC", True), _row(RUNS[3], "CCC", False),
        _row(RUNS[4], "CCC", True),
    ]
    episodes = reconstruct_episodes(rows, "CCC", "daily")
    assert len(episodes) == 3
    assert len(prior_episodes(episodes)) == 2
    assert "2 times" in format_episode_history(episodes)


def test_timeframe_is_isolated() -> None:
    # A weekly episode must not bleed into the daily reconstruction.
    rows = _aaa_rows() + [_row(RUNS[0], "AAA", True, tf="weekly")]
    daily = reconstruct_episodes(rows, "AAA", "daily")
    assert len(daily) == 2  # the weekly row is ignored


def test_unknown_ticker_and_empty_rows() -> None:
    assert reconstruct_episodes(_aaa_rows(), "ZZZ", "daily") == []
    assert reconstruct_episodes([], "AAA", "daily") == []
