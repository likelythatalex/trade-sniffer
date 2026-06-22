"""Tests for the plan-outcome simulator + policy sweep (backtest/plan_sim.py).

Mostly pure: hand-built trials (Levels + a tiny forward OHLC path) resolved under the real
TradePlanConfig, the policy aggregate on a synthetic outcomes frame, and the sweep mechanics.
Plus one hermetic replay→collect→simulate→sweep smoke (fetch monkeypatched, no network).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src import config as config_module
from src.backtest import plan_sim, replay
from src.data import FetchResult
from src.strategies.base import Levels

CONFIG = config_module.load_config(Path("config.yaml"))
PLAN = CONFIG.trade_plan


def _forward(bars: list[tuple[float, float]]) -> pd.DataFrame:
    """Forward OHLC frame from (high, low) pairs; open/close set mid (irrelevant to fill/stop)."""
    idx = pd.bdate_range("2024-02-01", periods=len(bars))
    high = [h for h, _ in bars]
    low = [low for _, low in bars]
    mid = [(h + low) / 2 for h, low in bars]
    return pd.DataFrame({"open": mid, "high": high, "low": low, "close": mid, "volume": [100.0] * len(bars)}, index=idx)


def _trial(forward: pd.DataFrame, direction: str = "accumulation", when="2024-01-15") -> plan_sim.Trial:
    levels = Levels(range_high=110.0, range_low=100.0, spring_low=98.0, upthrust_high=112.0, atr=2.0)
    return plan_sim.Trial("AAA", pd.Timestamp(when), direction, levels, forward)


# --- _first_fill --------------------------------------------------------------


def test_first_fill_long_and_short() -> None:
    fwd = _forward([(108, 100), (111, 105), (115, 109)])
    assert plan_sim._first_fill("accumulation", 110.0, fwd) == 1   # first bar with high >= 110
    # short fills when low <= entry; entry 100 here -> first bar with low <= 100 is bar 0
    assert plan_sim._first_fill("distribution", 100.0, fwd) == 0
    assert plan_sim._first_fill("accumulation", 200.0, fwd) is None  # never triggers


# --- simulate (resolution categories) -----------------------------------------


def test_simulate_target_stop_nofill_open() -> None:
    # entry=110 (range_high), target=120 (measured move), stop < 110 (capped/structural).
    target = _trial(_forward([(111, 109), (121, 115)]))           # fills bar0, target bar1
    stop = _trial(_forward([(111, 109), (112, 90)]))              # fills bar0, deep low hits stop
    no_fill = _trial(_forward([(108, 100), (109, 101)]))          # never breaks out
    still_open = _trial(_forward([(111, 109)]))                   # fills on the last bar -> no path

    out = plan_sim.simulate([target, stop, no_fill, still_open], PLAN)
    res = list(out["resolution"])
    assert res == ["target", "stop", "no_fill", "open"]
    assert out.loc[0, "realized_r"] > 0          # target → +R
    assert out.loc[1, "realized_r"] == -1.0      # stop → −1R
    assert not out.loc[2, "filled"]              # no_fill → never entered
    assert pd.isna(out.loc[3, "realized_r"])     # open → unresolved


def test_simulate_short_direction_targets_downside() -> None:
    # Distribution: entry=100 (range_low breakdown), target=90. Fill when low<=100, then run down.
    short = _trial(_forward([(101, 99), (95, 89)]), direction="distribution")
    out = plan_sim.simulate([short], PLAN)
    assert out.loc[0, "resolution"] == "target" and out.loc[0, "realized_r"] > 0


# --- summarize_policy ---------------------------------------------------------


def test_summarize_policy_math() -> None:
    outcomes = pd.DataFrame([
        {"filled": True, "resolution": "target", "realized_r": 2.0, "mfe_r": 2.0, "mae_r": 0.3, "bars_held": 4},
        {"filled": True, "resolution": "stop", "realized_r": -1.0, "mfe_r": 0.5, "mae_r": 1.0, "bars_held": 2},
        {"filled": True, "resolution": "target", "realized_r": 1.5, "mfe_r": 1.5, "mae_r": 0.4, "bars_held": 6},
        {"filled": True, "resolution": "open", "realized_r": None, "mfe_r": 0.8, "mae_r": 0.5, "bars_held": 0},
        {"filled": False, "resolution": "no_fill", "realized_r": None, "mfe_r": float("nan"), "mae_r": float("nan"), "bars_held": 0},
    ])
    s = plan_sim.summarize_policy(outcomes)
    assert s["n_trials"] == 5 and s["n_resolved"] == 3
    assert s["fill_rate"] == pytest.approx(4 / 5)
    assert s["win_rate"] == pytest.approx(2 / 3)
    assert s["expectancy_r"] == pytest.approx((2.0 - 1.0 + 1.5) / 3)
    assert s["profit_factor"] == pytest.approx(3.5 / 1.0)
    assert s["target_rate"] == pytest.approx(0.5) and s["open_rate"] == pytest.approx(0.25)


def test_summarize_policy_empty_is_safe() -> None:
    s = plan_sim.summarize_policy(pd.DataFrame())
    assert s["n_trials"] == 0 and pd.isna(s["expectancy_r"])


# --- sweep + split ------------------------------------------------------------


def test_split_by_date() -> None:
    trials = [_trial(_forward([(111, 109)]), when=f"2024-01-{d:02d}") for d in (10, 11, 12, 13)]
    in_sample, oos = plan_sim._split_by_date(trials, 0.5)
    assert [t.date.day for t in in_sample] == [10, 11]  # earliest in-sample
    assert oos is not None and [t.date.day for t in oos] == [12, 13]  # latest held out
    assert plan_sim._split_by_date(trials, 0.0) == (trials, None)  # no split


def test_sweep_runs_grid_and_sorts() -> None:
    trials = [
        _trial(_forward([(111, 109), (121, 115)]), when="2024-01-10"),  # winner
        _trial(_forward([(111, 109), (112, 90)]), when="2024-01-11"),   # loser
    ]
    grid = {"stop_method": ["capped", "structural"], "max_stop_pct": [5.0, 12.0]}
    df = plan_sim.sweep(trials, PLAN, grid)
    assert len(df) == 4  # full cartesian product
    assert {"stop_method", "max_stop_pct", "expectancy_r", "win_rate"}.issubset(df.columns)
    # sorted by expectancy_r descending (NaNs last)
    finite = df["expectancy_r"].dropna()
    assert finite.is_monotonic_decreasing


def test_sweep_adds_oos_columns_when_split() -> None:
    trials = [_trial(_forward([(111, 109), (121, 115)]), when=f"2024-01-{d:02d}") for d in (10, 11, 12, 13)]
    df = plan_sim.sweep(trials, PLAN, {"stop_method": ["capped"], "max_stop_pct": [8.0]}, oos_frac=0.5)
    assert "oos_expectancy_r" in df.columns and "oos_n_resolved" in df.columns


# --- hermetic end-to-end (no network) -----------------------------------------


def _long_df(n: int = 120) -> pd.DataFrame:
    idx = pd.bdate_range("2023-01-02", periods=n)
    closes = [120.0 - i * 0.5 if i < n // 2 else 95.0 + (i % 5) for i in range(n)]  # downtrend -> range
    close = pd.Series(closes, index=idx)
    return pd.DataFrame(
        {"open": close, "high": close + 1.0, "low": close - 1.0, "close": close, "volume": [100.0] * n}, index=idx
    )


def test_collect_simulate_sweep_smoke(monkeypatch: pytest.MonkeyPatch) -> None:
    df = _long_df(120)
    fr = FetchResult(df=df, exchange=None, corporate_actions=pd.Series(dtype=float), expected_sessions=None)
    monkeypatch.setattr(plan_sim, "fetch_many", lambda tickers, tf, c, today=None: {"AAA": fr})
    monkeypatch.setattr(replay, "fetch_spy", lambda tf, c, today=None: pd.DataFrame({"close": df["close"]}))

    trials = plan_sim.collect_trials(["AAA"], "daily", CONFIG, step=5)
    assert trials and all(t.direction in ("accumulation", "distribution") for t in trials)
    assert all(not t.forward.empty for t in trials)

    sweep_df = plan_sim.sweep(trials, PLAN, {"stop_method": ["capped", "structural"], "max_stop_pct": [8.0]})
    assert len(sweep_df) == 2 and "expectancy_r" in sweep_df.columns
