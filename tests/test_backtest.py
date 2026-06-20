"""Tests for the offline backtester (Tier 3).

Pure metric/outcome math on hand-built data, plus a hermetic replay smoke (fetch
monkeypatched, no network). We assert the *properties* a backtester must have — IC sign,
positive lift when the signal works, forward-return math — not pinned magnitudes.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src import config as config_module
from src.backtest import replay
from src.backtest.metrics import compute_report, hit_rate_lift, information_coefficient
from src.backtest.outcomes import _series_forward_returns, add_forward_returns
from src.backtest.report import render_report
from src.data import FetchResult

CONFIG = config_module.load_config(Path("config.yaml"))


# --- outcomes -----------------------------------------------------------------


def test_series_forward_returns() -> None:
    close = pd.Series([100.0, 110.0, 121.0])
    fwd = _series_forward_returns(close, 1)
    assert fwd.iloc[0] == pytest.approx(0.10)
    assert fwd.iloc[1] == pytest.approx(0.10)
    assert pd.isna(fwd.iloc[2])  # no bar one ahead -> NaN


def test_add_forward_returns_with_excess() -> None:
    idx = pd.bdate_range("2024-01-01", periods=3)
    prices = {"A": pd.Series([100.0, 110.0, 121.0], index=idx)}
    benchmark = pd.Series([100.0, 100.0, 100.0], index=idx)  # flat market
    signals = pd.DataFrame({"ticker": ["A", "A"], "date": [idx[0], idx[1]]})
    out = add_forward_returns(signals, prices, benchmark, [1])
    assert out["fwd_return_1"].iloc[0] == pytest.approx(0.10)
    # excess strips the (flat) market -> equals the raw return here
    assert out["excess_return_1"].iloc[0] == pytest.approx(0.10)


def test_excess_return_is_nan_without_benchmark() -> None:
    idx = pd.bdate_range("2024-01-01", periods=2)
    signals = pd.DataFrame({"ticker": ["A"], "date": [idx[0]]})
    out = add_forward_returns(signals, {"A": pd.Series([100.0, 110.0], index=idx)}, None, [1])
    assert out["fwd_return_1"].iloc[0] == pytest.approx(0.10)
    assert pd.isna(out["excess_return_1"].iloc[0])


# --- metrics ------------------------------------------------------------------


def test_information_coefficient_sign() -> None:
    monotonic = pd.DataFrame(
        {"signed_score": [-80, -40, 0, 40, 80, 90], "excess_return_5": [-0.05, -0.02, 0.0, 0.02, 0.05, 0.06]}
    )
    assert information_coefficient(monotonic, "signed_score", "excess_return_5") > 0.9

    inverted = monotonic.assign(excess_return_5=monotonic["excess_return_5"][::-1].to_numpy())
    assert information_coefficient(inverted, "signed_score", "excess_return_5") < -0.9


def test_information_coefficient_is_nan_safe() -> None:
    constant = pd.DataFrame({"signed_score": [10, 10, 10], "excess_return_5": [0.01, 0.02, 0.03]})
    assert pd.isna(information_coefficient(constant, "signed_score", "excess_return_5"))


def test_hit_rate_lift_positive_when_signal_works() -> None:
    df = pd.DataFrame(
        {
            "signed_score": [80, 80, 80, -80, -80, -80, 0],
            "excess_return_5": [0.02, 0.03, -0.01, -0.02, -0.03, 0.01, 0.0],
        }
    )
    hr = hit_rate_lift(df, "excess_return_5", "signed_score")
    assert hr["accumulation_lift"] > 0  # accumulation flags rise more than the base rate
    assert hr["distribution_lift"] > 0  # distribution flags fall more than the base rate


def test_compute_report_structure() -> None:
    df = pd.DataFrame(
        {
            "signed_score": [80, -80, 0],
            "sub_wyckoff.volume_behavior": [60.0, -50.0, 0.0],
            "fwd_return_5": [0.03, -0.02, 0.0],
            "excess_return_5": [0.02, -0.01, 0.0],
        }
    )
    report = compute_report(df, [5])
    assert report["n_signals"] == 3
    assert 5 in report["horizons"]
    assert "sub_wyckoff.volume_behavior" in report["horizons"][5]["subscore_ic"]


# --- replay (hermetic smoke) --------------------------------------------------


def _long_df(n: int = 120) -> pd.DataFrame:
    idx = pd.bdate_range("2023-01-02", periods=n)
    closes = [120.0 - i * 0.5 if i < n // 2 else 95.0 + (i % 5) for i in range(n)]  # downtrend -> range
    close = pd.Series(closes, index=idx)
    return pd.DataFrame(
        {"open": close, "high": close + 1.0, "low": close - 1.0, "close": close, "volume": [100.0] * n}, index=idx
    )


def test_replay_produces_signals_and_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    df = _long_df(120)
    fr = FetchResult(df=df, exchange=None, corporate_actions=pd.Series(dtype=float), expected_sessions=None)
    monkeypatch.setattr(replay, "fetch_many", lambda tickers, tf, c, today=None: {"AAA": fr})
    monkeypatch.setattr(replay, "fetch_spy", lambda tf, c, today=None: pd.DataFrame({"close": df["close"]}))

    signals, prices, benchmark = replay.replay_history(["AAA"], "daily", CONFIG)

    assert not signals.empty
    assert {"ticker", "date", "signed_score", "direction", "composite_score"}.issubset(signals.columns)
    assert "AAA" in prices
    assert signals["date"].is_monotonic_increasing  # scored forward in time
    assert benchmark is not None


def test_replay_end_to_end_to_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    df = _long_df(120)
    fr = FetchResult(df=df, exchange=None, corporate_actions=pd.Series(dtype=float), expected_sessions=None)
    monkeypatch.setattr(replay, "fetch_many", lambda tickers, tf, c, today=None: {"AAA": fr})
    monkeypatch.setattr(replay, "fetch_spy", lambda tf, c, today=None: pd.DataFrame({"close": df["close"]}))

    signals, prices, benchmark = replay.replay_history(["AAA"], "daily", CONFIG)
    enriched = add_forward_returns(signals, prices, benchmark, [5])
    report = compute_report(enriched, [5])
    path = render_report(report, enriched, tmp_path, "daily")

    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "Replay caveat" in text and "IC" in text
    assert path.with_name(path.name.replace(".md", "_rows.csv")).exists()
