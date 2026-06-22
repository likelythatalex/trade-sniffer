"""Tests for the failed→revived event study (backtest/event_study.py).

Pure logic on synthetic signals.csv rows (string-typed, as report.read_signals yields):
close-path excursions, cohort labelling (first vs revived), and the comparison aggregate.
"""
from __future__ import annotations

from src.backtest import event_study as es

RUNS = [f"2026-01-{day:02d}T22:00:00+00:00" for day in range(1, 13)]


def _row(run_ts, ticker, made, close, direction="accumulation", tf="daily"):
    return {
        "run_ts": run_ts, "ticker": ticker, "timeframe": tf, "direction": direction,
        "made_watchlist": str(made), "composite_score": "60.0", "close": str(close),
    }


# --- close_path_excursion -----------------------------------------------------


def test_excursion_accumulation_reaches_target() -> None:
    # Entry 100, then +5%, +12% -> MFE 0.12, target 0.10 hit on the 2nd forward run.
    ex = es.close_path_excursion(100.0, [105.0, 112.0], sign=1.0, target=0.10)
    assert round(ex.mfe, 4) == 0.12 and ex.mae == 0.0
    assert ex.reached_target is True and ex.bars_to_target == 2
    assert ex.n_forward == 2


def test_excursion_distribution_favorable_is_down() -> None:
    # Short setup: price falling is favorable. Entry 100 -> 90 = +10% favorable.
    ex = es.close_path_excursion(100.0, [95.0, 90.0], sign=-1.0, target=0.10)
    assert round(ex.mfe, 4) == 0.10 and ex.reached_target is True and ex.bars_to_target == 2


def test_excursion_adverse_only_records_mae_not_target() -> None:
    # Accumulation that only falls: MFE clamped at 0, MAE positive, target never reached.
    ex = es.close_path_excursion(100.0, [98.0, 95.0], sign=1.0, target=0.10)
    assert ex.mfe == 0.0 and round(ex.mae, 4) == 0.05
    assert ex.reached_target is False and ex.bars_to_target is None


# --- build_excursions ---------------------------------------------------------


def _two_episode_rows():
    # AAA: episode 1 (runs 1-2, qualifying), drops off (run 3 non-qualifying), revives run 4-5.
    # Closes keep being logged every run (the forward path), incl. while non-qualifying.
    return [
        _row(RUNS[0], "AAA", True, 100.0),
        _row(RUNS[1], "AAA", True, 102.0),
        _row(RUNS[2], "AAA", False, 101.0),
        _row(RUNS[3], "AAA", True, 100.0),   # revival entry
        _row(RUNS[4], "AAA", True, 108.0),
        _row(RUNS[5], "AAA", False, 112.0),  # +12% from revival entry, after it dropped off
    ]


def test_build_excursions_labels_first_and_revived() -> None:
    df = es.build_excursions(_two_episode_rows(), "daily", target=0.10, max_horizon=60)
    by_cohort = {row["cohort"]: row for _, row in df.iterrows()}
    assert set(by_cohort) == {"first", "revived"}

    revived = by_cohort["revived"]
    assert revived["entry_ts"] == RUNS[3] and revived["entry_close"] == 100.0
    assert round(revived["mfe"], 4) == 0.12 and revived["reached_target"] is True
    assert revived["n_forward"] == 2  # runs 5 and 6 after the revival entry


def test_build_excursions_drops_episode_with_no_forward() -> None:
    # A revival on the very last logged run has no forward closes -> excluded (nothing to measure).
    rows = [
        _row(RUNS[0], "BBB", True, 100.0),
        _row(RUNS[1], "BBB", False, 101.0),
        _row(RUNS[2], "BBB", True, 100.0),  # revives on the last run -> no forward path
    ]
    df = es.build_excursions(rows, "daily", target=0.10, max_horizon=60)
    assert (df["cohort"] == "revived").sum() == 0  # the forward-less revival is dropped
    assert (df["cohort"] == "first").sum() == 1    # first episode still has forward closes


def test_max_horizon_caps_forward_path() -> None:
    df = es.build_excursions(_two_episode_rows(), "daily", target=0.10, max_horizon=1)
    revived = df[df["cohort"] == "revived"].iloc[0]
    assert revived["n_forward"] == 1  # only the first forward run counted
    assert round(revived["mfe"], 4) == 0.08  # 108 vs 100; the +12% run is beyond the horizon


# --- summarize ----------------------------------------------------------------


def test_summarize_compares_cohorts() -> None:
    summary = es.summarize(
        es.build_excursions(_two_episode_rows(), "daily", 0.10, 60), 0.10, 60, "daily"
    )
    assert summary["cohorts"]["revived"]["n"] == 1
    assert summary["cohorts"]["revived"]["fulfilment_rate"] == 1.0
    assert summary["cohorts"]["first"]["n"] == 1
    assert summary["n_episodes"] == 2


def test_summarize_empty_is_safe() -> None:
    import pandas as pd

    summary = es.summarize(pd.DataFrame(), 0.10, 60, "daily")
    assert summary["n_episodes"] == 0
    assert summary["cohorts"]["revived"]["n"] == 0


def test_render_markdown_smoke() -> None:
    summary = es.summarize(
        es.build_excursions(_two_episode_rows(), "daily", 0.10, 60), 0.10, 60, "daily"
    )
    md = es.render_markdown(summary, "rows.csv", "2026-06-22")
    assert "Failed→revived event study" in md
    assert "revived" in md and "first-time" in md and "survivorship" in md
