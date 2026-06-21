"""Tests for the market-context layer (SPEC §12).

Pure + hermetic: regime label (SPY vs MA blended with breadth), breadth over names with
enough history, and graceful abstain ("unknown") when SPY can't be judged. Also that
config resolves the per-timeframe MA window.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src import config as config_module
from src.market_context import compute_market_context

CONFIG = config_module.load_config(Path("config.yaml"))

_RISING = pd.Series([10.0, 11, 12, 13, 14, 15])   # last close above its trailing MA
_FALLING = pd.Series([15.0, 14, 13, 12, 11, 10])  # last close below its trailing MA
MA = 5


def test_risk_on_when_spy_up_and_breadth_healthy() -> None:
    ctx = compute_market_context(_RISING, [_RISING, _RISING, _FALLING], MA)  # 2/3 above
    assert ctx.regime == "risk-on"
    assert ctx.spy_above_ma is True and ctx.spy_distance_pct > 0
    assert ctx.breadth_pct > 50 and ctx.n_breadth == 3 and ctx.ma_window == MA


def test_risk_off_when_spy_down_and_breadth_weak() -> None:
    ctx = compute_market_context(_FALLING, [_FALLING, _FALLING, _RISING], MA)  # 1/3 above
    assert ctx.regime == "risk-off"
    assert ctx.spy_above_ma is False and ctx.breadth_pct < 50


def test_neutral_on_mixed_signals() -> None:
    # SPY up but weak breadth -> mixed -> neutral.
    ctx = compute_market_context(_RISING, [_FALLING, _FALLING, _RISING], MA)
    assert ctx.regime == "neutral"


def test_unknown_when_spy_history_too_short() -> None:
    short = pd.Series([1.0, 2.0])  # fewer than ma_window bars
    ctx = compute_market_context(short, [_RISING], MA)
    assert ctx.regime == "unknown" and ctx.spy_above_ma is None and ctx.spy_distance_pct is None


def test_breadth_excludes_names_without_enough_history() -> None:
    short = pd.Series([1.0, 2.0])  # too short -> not counted in the denominator
    ctx = compute_market_context(_RISING, [_RISING, short], MA)
    assert ctx.n_breadth == 1 and ctx.breadth_pct == 100.0


def test_breadth_none_when_nothing_measurable() -> None:
    ctx = compute_market_context(_RISING, [pd.Series([1.0, 2.0])], MA)
    assert ctx.breadth_pct is None and ctx.n_breadth == 0


def test_config_resolves_per_timeframe_ma_window() -> None:
    assert int(config_module.resolve_market_params(CONFIG, "daily")["ma_window"]) == 200
    assert int(config_module.resolve_market_params(CONFIG, "weekly")["ma_window"]) == 40
