"""Tests for the scanner orchestrator (SPEC §3, §10).

The row/card mappers are unit-tested precisely. run_timeframe is tested end-to-end
with data.fetch_many + resolve_exchange + load_universe monkeypatched (no network),
asserting the pipeline produces a report + signals.csv and counts correctly.
"""
from __future__ import annotations

import csv
import dataclasses
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from src import config as config_module
from src import scanner
from src.data import FetchResult
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


def _patch_fetch(monkeypatch: pytest.MonkeyPatch, mapping: dict[str, FetchResult]) -> None:
    """Stub the batch fetch, lazy exchange, and SPY benchmark so run_timeframe stays
    hermetic. Benchmark defaults to None (RS abstains) → existing scores unchanged."""
    monkeypatch.setattr(scanner, "fetch_many", lambda tickers, tf, c, today=None: mapping)
    monkeypatch.setattr(scanner, "resolve_exchange", lambda _t, _c: "NYSE")
    monkeypatch.setattr(scanner, "_benchmark_close", lambda _tf, _c, _today: None)


def test_run_timeframe_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = config_for(tmp_path)
    fetch_result = FetchResult(df=accumulation_df(), exchange=None, corporate_actions=pd.Series(dtype=float))
    monkeypatch.setattr(scanner, "load_universe", lambda _path: ["XOM"])
    _patch_fetch(monkeypatch, {"XOM": fetch_result})

    counts = scanner.run_timeframe("daily", cfg, today=date(2024, 6, 1))

    assert counts["scanned"] == 1
    assert (Path(cfg.output.dir) / "report_daily_2024-06-01.html").exists()
    assert (Path(cfg.output.dir) / "index.html").exists()  # landing page written
    signals = Path(cfg.output.dir) / "signals.csv"
    assert signals.exists()
    lines = signals.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2  # header + the one evaluated ticker


def test_run_timeframe_skips_ticker_with_no_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A ticker the batch returned nothing for is absent from the result -> skipped, no crash.
    cfg = config_for(tmp_path)
    monkeypatch.setattr(scanner, "load_universe", lambda _path: ["BAD"])
    _patch_fetch(monkeypatch, {})

    counts = scanner.run_timeframe("daily", cfg, today=date(2024, 6, 1))
    assert counts["scanned"] == 1 and counts["skipped"] == 1


def test_run_timeframe_fail_soft_on_eval_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # An exception while evaluating one ticker is caught (errored), never aborts the run.
    cfg = config_for(tmp_path)
    fetch_result = FetchResult(df=accumulation_df(), exchange=None, corporate_actions=pd.Series(dtype=float))
    monkeypatch.setattr(scanner, "load_universe", lambda _path: ["XOM"])
    _patch_fetch(monkeypatch, {"XOM": fetch_result})

    class _BoomStrategy:
        name = "wyckoff"

        def evaluate(self, df, context):
            raise ValueError("kaboom")

    monkeypatch.setattr(scanner, "get_strategy", lambda _name: _BoomStrategy())
    counts = scanner.run_timeframe("daily", cfg, today=date(2024, 6, 1))
    assert counts["scanned"] == 1 and counts["errored"] == 1


def test_run_timeframe_logs_rs_when_benchmark_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # With a benchmark wired in, the RS contribution is logged to signals.csv (not "").
    cfg = config_for(tmp_path)
    stock = accumulation_df()
    fetch_result = FetchResult(df=stock, exchange=None, corporate_actions=pd.Series(dtype=float))
    monkeypatch.setattr(scanner, "load_universe", lambda _path: ["XOM"])
    monkeypatch.setattr(scanner, "fetch_many", lambda tickers, tf, c, today=None: {"XOM": fetch_result})
    monkeypatch.setattr(scanner, "resolve_exchange", lambda _t, _c: "NYSE")
    spy = pd.Series([float(120 - i) for i in range(len(stock))], index=stock.index)  # falling SPY
    monkeypatch.setattr(scanner, "_benchmark_close", lambda _tf, _c, _today: spy)

    scanner.run_timeframe("daily", cfg, today=date(2024, 6, 1))

    rows = list(csv.DictReader((Path(cfg.output.dir) / "signals.csv").read_text(encoding="utf-8").splitlines()))
    assert rows[-1]["rs_vs_spy"] != ""  # RS contribution recorded for the evaluated bar


def test_run_timeframe_uses_explicit_tickers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = config_for(tmp_path)
    fetch_result = FetchResult(df=accumulation_df(), exchange=None, corporate_actions=pd.Series(dtype=float))
    seen: list[str] = []

    def fake_fetch_many(tickers, tf, c, today=None):
        seen.extend(tickers)
        return {ticker: fetch_result for ticker in tickers}

    # load_universe must NOT be consulted when explicit tickers are given.
    def fail_universe(_path):
        raise AssertionError("load_universe should not be called in on-demand mode")

    monkeypatch.setattr(scanner, "load_universe", fail_universe)
    monkeypatch.setattr(scanner, "fetch_many", fake_fetch_many)
    monkeypatch.setattr(scanner, "resolve_exchange", lambda _t, _c: "NYSE")
    monkeypatch.setattr(scanner, "_benchmark_close", lambda _tf, _c, _today: None)

    counts = scanner.run_timeframe("daily", cfg, today=date(2024, 6, 1), tickers=["COIN", "PLTR"])
    assert seen == ["COIN", "PLTR"]
    assert counts["scanned"] == 2


def test_no_liquidity_gate_keeps_thin_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Real liquidity floor would skip this low-$-volume fixture; the bypass keeps it.
    out = dataclasses.replace(CONFIG.output, dir=str(tmp_path))
    cfg = dataclasses.replace(CONFIG, output=out)  # real liquidity floor in force
    fetch_result = FetchResult(df=accumulation_df(), exchange=None, corporate_actions=pd.Series(dtype=float))
    monkeypatch.setattr(scanner, "load_universe", lambda _p: ["THIN"])
    _patch_fetch(monkeypatch, {"THIN": fetch_result})

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
    fetch_result = FetchResult(df=accumulation_df(), exchange=None, corporate_actions=pd.Series(dtype=float))
    monkeypatch.setattr(scanner, "load_universe", lambda _p: ["XOM"])
    _patch_fetch(monkeypatch, {"XOM": fetch_result})
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
