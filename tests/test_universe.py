"""Tests for universe loading + the liquidity gate (SPEC §4.1, §6.5)."""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pandas as pd

from src.config import LiquidityConfig
from src.universe import load_universe, passes_liquidity_gate


def liquidity_config(**overrides) -> LiquidityConfig:
    base = LiquidityConfig(min_avg_dollar_volume=20_000_000.0, min_price=5.0)
    return dataclasses.replace(base, **overrides)


def ohlcv(price: float, volume: float, n: int = 25) -> pd.DataFrame:
    return pd.DataFrame({"open": [price] * n, "high": [price] * n, "low": [price] * n, "close": [price] * n, "volume": [volume] * n})


def test_load_universe_skips_comments_and_blanks(tmp_path: Path) -> None:
    f = tmp_path / "universe.txt"
    f.write_text("# header comment\n\nAAPL\nmsft\n  GOOGL  \n# trailing\n", encoding="utf-8")
    assert load_universe(f) == ["AAPL", "MSFT", "GOOGL"]


def test_load_universe_dedups_preserving_order(tmp_path: Path) -> None:
    f = tmp_path / "universe.txt"
    f.write_text("AAPL\nMSFT\nAAPL\n", encoding="utf-8")
    assert load_universe(f) == ["AAPL", "MSFT"]


def test_shipped_universe_loads() -> None:
    tickers = load_universe(Path("universe.txt"))
    assert "AAPL" in tickers and len(tickers) > 0


def test_liquid_stock_passes() -> None:
    df = ohlcv(price=100.0, volume=1_000_000)  # $100M/day
    ok, reason = passes_liquidity_gate(df, liquidity_config())
    assert ok is True and reason is None


def test_thin_dollar_volume_fails() -> None:
    df = ohlcv(price=10.0, volume=1_000)  # $10k/day
    ok, reason = passes_liquidity_gate(df, liquidity_config())
    assert ok is False and "volume" in (reason or "")


def test_penny_price_fails() -> None:
    df = ohlcv(price=2.0, volume=100_000_000)  # plenty of $vol but sub-$5
    ok, reason = passes_liquidity_gate(df, liquidity_config())
    assert ok is False and "price" in (reason or "")


def test_empty_frame_fails() -> None:
    ok, reason = passes_liquidity_gate(pd.DataFrame(), liquidity_config())
    assert ok is False and reason
