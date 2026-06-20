"""Tests for the path-dependent trade outcome evaluator (SPEC §8A.2).

Hand-built forward OHLC frames pin each resolution path (target-first, stop-first, the
conservative both-in-one-bar tie-break, still-open), plus MFE/MAE in R units.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.trade_outcome import evaluate_outcome


def forward(highs, lows) -> pd.DataFrame:
    # close isn't used by the evaluator (high/low decide touches); fill it for completeness.
    return pd.DataFrame({"high": highs, "low": lows, "close": lows})


# Long trade: entry 100, stop 90 (risk 10), target 120 (reward 20 -> 2R).


def test_long_target_first() -> None:
    out = evaluate_outcome("long", 100.0, 90.0, 120.0, forward([105, 121], [98, 110]))
    assert out.resolution == "target"
    assert out.realized_r == pytest.approx(2.0)  # reward:risk
    assert out.bars_held == 2
    assert out.mfe_r == pytest.approx((121 - 100) / 10)  # 2.1R high-water


def test_long_stop_first() -> None:
    out = evaluate_outcome("long", 100.0, 90.0, 120.0, forward([105, 106], [98, 89]))
    assert out.resolution == "stop"
    assert out.realized_r == pytest.approx(-1.0)
    assert out.mae_r == pytest.approx((100 - 89) / 10)  # 1.1R worst drawdown


def test_both_in_one_bar_counts_stop_first() -> None:
    # A single bar spans stop (89) AND target (121) -> conservative: stop wins.
    out = evaluate_outcome("long", 100.0, 90.0, 120.0, forward([121], [89]))
    assert out.resolution == "stop"
    assert out.realized_r == pytest.approx(-1.0)


def test_open_when_neither_hit() -> None:
    out = evaluate_outcome("long", 100.0, 90.0, 120.0, forward([108, 109, 110], [95, 96, 97]))
    assert out.resolution == "open"
    assert out.realized_r is None
    assert out.bars_held == 3  # evaluated all available bars
    assert out.mfe_r == pytest.approx((110 - 100) / 10)


# Short trade mirror: entry 100, stop 110 (risk 10), target 80 (reward 20 -> 2R).


def test_short_target_first() -> None:
    out = evaluate_outcome("short", 100.0, 110.0, 80.0, forward([104, 101], [96, 79]))
    assert out.resolution == "target"
    assert out.realized_r == pytest.approx(2.0)


def test_short_stop_first() -> None:
    out = evaluate_outcome("short", 100.0, 110.0, 80.0, forward([105, 111], [96, 100]))
    assert out.resolution == "stop"
    assert out.realized_r == pytest.approx(-1.0)


# Degenerate / empty -> None (no crash).


def test_zero_risk_returns_none() -> None:
    assert evaluate_outcome("long", 100.0, 100.0, 120.0, forward([121], [90])) is None


def test_no_forward_data_returns_none() -> None:
    assert evaluate_outcome("long", 100.0, 90.0, 120.0, forward([], [])) is None


def test_bad_direction_returns_none() -> None:
    assert evaluate_outcome("sideways", 100.0, 90.0, 120.0, forward([121], [90])) is None
