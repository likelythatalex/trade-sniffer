"""Tests for data.py pure helpers (SPEC §5.1).

The network fetch is exercised by a manual run, not here — these cover the pure,
deterministic logic so it stays fast and hermetic (no yfinance calls).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from src import config as config_module
from src.data import (
    DataError,
    _cache_path,
    _fetch_window,
    _interval,
    _load_overrides,
    _normalize_columns,
    _resolve_exchange,
)

CONFIG = config_module.load_config(Path("config.yaml"))


def test_interval_maps_timeframe() -> None:
    assert _interval("daily") == "1d"
    assert _interval("weekly") == "1wk"
    with pytest.raises(DataError):
        _interval("hourly")


def test_fetch_window_uses_configured_lookback() -> None:
    today = date(2024, 6, 1)
    daily_start = _fetch_window("daily", CONFIG, today)
    weekly_start = _fetch_window("weekly", CONFIG, today)
    assert (today - daily_start).days == CONFIG.data.daily_lookback_days
    assert (today - weekly_start).days == CONFIG.data.weekly_lookback_weeks * 7


def test_normalize_columns_lowercases_and_subsets() -> None:
    raw = pd.DataFrame(
        {
            "Open": [1.0],
            "High": [2.0],
            "Low": [0.5],
            "Close": [1.5],
            "Volume": [100.0],
            "Dividends": [0.0],  # extra columns dropped
        }
    )
    out = _normalize_columns(raw, "AAPL")
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]
    assert out["close"].iloc[0] == 1.5


def test_normalize_columns_raises_on_missing() -> None:
    with pytest.raises(DataError, match="missing"):
        _normalize_columns(pd.DataFrame({"Open": [1.0]}), "AAPL")


def test_resolve_exchange_override_wins() -> None:
    metadata = {"fullExchangeName": "NasdaqGS"}
    assert _resolve_exchange(metadata, "AAPL", {"AAPL": "NASDAQ"}) == "NASDAQ"


def test_resolve_exchange_falls_back_to_metadata_then_none() -> None:
    assert _resolve_exchange({"fullExchangeName": "NasdaqGS"}, "MSFT", {}) == "NasdaqGS"
    assert _resolve_exchange({}, "MSFT", {}) is None


def test_load_overrides_skips_comments_and_header(tmp_path: Path) -> None:
    csv = tmp_path / "overrides.csv"
    csv.write_text("# a comment\nticker,exchange\nBRK-B,NYSE\n", encoding="utf-8")
    assert _load_overrides(csv) == {"BRK-B": "NYSE"}


def test_load_overrides_missing_file_is_empty(tmp_path: Path) -> None:
    assert _load_overrides(tmp_path / "nope.csv") == {}


def test_cache_path_format() -> None:
    path = _cache_path(".cache", "AAPL", "daily", date(2024, 6, 1))
    assert path.name == "AAPL_daily_2024-06-01.pkl"
