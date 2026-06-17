"""Tests for the scanner orchestrator (SPEC §3, §10).

The row/card mappers are unit-tested precisely. run_timeframe is tested end-to-end
with data.fetch_ohlcv + load_universe monkeypatched (no network), asserting the
pipeline produces a report + signals.csv and counts correctly.
"""
from __future__ import annotations

import dataclasses
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from src import config as config_module
from src import scanner
from src.data import DataError, FetchResult
from src.strategies.base import StrategyResult
from tests.test_wyckoff import accumulation_df

CONFIG = config_module.load_config(Path("config.yaml"))


def config_for(tmp_path: Path) -> config_module.Config:
    """Real config but output -> tmp and liquidity floor removed (test data is small)."""
    out = dataclasses.replace(CONFIG.output, dir=str(tmp_path))
    liq = dataclasses.replace(CONFIG.liquidity, min_avg_dollar_volume=0.0, min_price=0.0)
    return dataclasses.replace(CONFIG, output=out, liquidity=liq)


def test_signals_row_matches_schema() -> None:
    composite = StrategyResult(direction="accumulation", score=72.0)
    wyckoff = StrategyResult(
        direction="accumulation", score=72.0,
        sub_scores={"range_structure": 10.0, "volume_behavior": 80.0, "spring_upthrust": 100.0, "confirmation": 40.0},
    )
    features = pd.DataFrame(
        {"volume_ratio": [1.5], "volume_pctile": [90.0], "spread_atr": [0.4], "spread_pctile": [80.0], "close_position": [0.2]}
    )
    row = scanner._signals_row("2024-06-01T22:00:00Z", "XOM", "daily", composite, wyckoff, features, _empty_quality(), True)
    assert set(row).issubset(set(scanner.SIGNALS_COLUMNS))
    assert row["ticker"] == "XOM"
    assert row["composite_score"] == 72.0
    assert row["volume_score"] == 80.0
    assert row["feat_close_position"] == 0.2
    assert row["transition"] == "none"
    assert row["made_watchlist"] is True


def test_card_shape() -> None:
    result = StrategyResult(direction="accumulation", score=72.0, sub_scores={"volume_behavior": 80.0}, reasons=["spring"])
    card = scanner._card("XOM", "NYSE", result)
    assert card == {
        "ticker": "XOM", "exchange": "NYSE", "direction": "accumulation",
        "score": 72.0, "sub_scores": {"volume_behavior": 80.0}, "reasons": ["spring"],
    }


def test_run_timeframe_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = config_for(tmp_path)
    fetch_result = FetchResult(df=accumulation_df(), exchange="NYSE", corporate_actions=pd.Series(dtype=float))
    monkeypatch.setattr(scanner, "load_universe", lambda _path: ["XOM"])
    monkeypatch.setattr(scanner, "fetch_ohlcv", lambda t, tf, c, today=None: fetch_result)

    counts = scanner.run_timeframe("daily", cfg, today=date(2024, 6, 1))

    assert counts["scanned"] == 1
    assert (Path(cfg.output.dir) / "report_daily_2024-06-01.html").exists()
    signals = Path(cfg.output.dir) / "signals.csv"
    assert signals.exists()
    lines = signals.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2  # header + the one evaluated ticker


def test_run_timeframe_fail_soft_on_data_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = config_for(tmp_path)

    def boom(*_args, **_kwargs):
        raise DataError("delisted")

    monkeypatch.setattr(scanner, "load_universe", lambda _path: ["BAD"])
    monkeypatch.setattr(scanner, "fetch_ohlcv", boom)

    counts = scanner.run_timeframe("daily", cfg, today=date(2024, 6, 1))
    assert counts["scanned"] == 1 and counts["skipped"] == 1  # logged + skipped, no crash


def test_run_timeframe_uses_explicit_tickers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = config_for(tmp_path)
    fetch_result = FetchResult(df=accumulation_df(), exchange="NYSE", corporate_actions=pd.Series(dtype=float))
    seen: list[str] = []

    def fake_fetch(ticker, tf, c, today=None):
        seen.append(ticker)
        return fetch_result

    # load_universe must NOT be consulted when explicit tickers are given.
    def fail_universe(_path):
        raise AssertionError("load_universe should not be called in on-demand mode")

    monkeypatch.setattr(scanner, "load_universe", fail_universe)
    monkeypatch.setattr(scanner, "fetch_ohlcv", fake_fetch)

    counts = scanner.run_timeframe("daily", cfg, today=date(2024, 6, 1), tickers=["COIN", "PLTR"])
    assert seen == ["COIN", "PLTR"]
    assert counts["scanned"] == 2


def test_no_liquidity_gate_keeps_thin_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Real liquidity floor would skip this low-$-volume fixture; the bypass keeps it.
    out = dataclasses.replace(CONFIG.output, dir=str(tmp_path))
    cfg = dataclasses.replace(CONFIG, output=out)  # real liquidity floor in force
    fetch_result = FetchResult(df=accumulation_df(), exchange="NYSE", corporate_actions=pd.Series(dtype=float))
    monkeypatch.setattr(scanner, "load_universe", lambda _p: ["THIN"])
    monkeypatch.setattr(scanner, "fetch_ohlcv", lambda t, tf, c, today=None: fetch_result)

    gated = scanner.run_timeframe("daily", cfg, today=date(2024, 6, 1), apply_liquidity_gate=True)
    assert gated["skipped"] == 1  # skipped by the gate

    ungated = scanner.run_timeframe("daily", cfg, today=date(2024, 6, 1), apply_liquidity_gate=False)
    assert ungated["skipped"] == 0  # evaluated despite thin volume


class _StubStrategy:
    name = "wyckoff"

    def evaluate(self, df, context):
        return StrategyResult("accumulation", 80.0, {"volume_behavior": 80.0}, ["stub"])


def test_dedup_transitions_and_state_across_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = config_for(tmp_path)  # liquidity floor removed; threshold 70 (stub scores 80 -> flags)
    fetch_result = FetchResult(df=accumulation_df(), exchange="NYSE", corporate_actions=pd.Series(dtype=float))
    monkeypatch.setattr(scanner, "load_universe", lambda _p: ["XOM"])
    monkeypatch.setattr(scanner, "fetch_ohlcv", lambda t, tf, c, today=None: fetch_result)
    monkeypatch.setattr(scanner, "get_strategy", lambda name: _StubStrategy())

    signals = Path(cfg.output.dir) / "signals.csv"

    # First run: XOM newly qualifies -> transition "new"; state persisted.
    scanner.run_timeframe("daily", cfg, today=date(2024, 6, 1))
    assert (Path(cfg.output.dir) / "state.json").exists()
    assert signals.read_text(encoding="utf-8").strip().splitlines()[-1].endswith("new")

    # Second run: still qualifying -> "continuing" (not re-notified as new).
    scanner.run_timeframe("daily", cfg, today=date(2024, 6, 2))
    assert signals.read_text(encoding="utf-8").strip().splitlines()[-1].endswith("continuing")


def _empty_quality():
    from src.data_quality import QualityReport

    return QualityReport()
