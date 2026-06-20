"""Tests for data.py pure helpers (SPEC §5.1).

The network fetch is exercised by a manual run, not here — these cover the pure,
deterministic logic so it stays fast and hermetic (no yfinance calls).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from src import config as config_module
from src.data import (
    DataError,
    _cache_path,
    _drop_incomplete_last_bar,
    _expected_sessions,
    _extract_splits,
    _fetch_window,
    _interval,
    _last_bar_is_incomplete,
    _load_overrides,
    _normalize_columns,
    _resolve_exchange,
    _slice_sessions,
    _split_batch,
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


def test_normalize_columns_makes_index_tz_naive_dates() -> None:
    # A tz-aware intraday index (as Ticker.history returns) must come out tz-naive at
    # midnight, so it aligns with the tz-naive yf.download path (needed for RS).
    idx = pd.DatetimeIndex(["2024-06-03 16:00:00"]).tz_localize("America/New_York")
    raw = pd.DataFrame(
        {"Open": [1.0], "High": [2.0], "Low": [0.5], "Close": [1.5], "Volume": [100.0]},
        index=idx,
    )
    out = _normalize_columns(raw, "AAPL")
    assert out.index.tz is None
    assert out.index[0] == pd.Timestamp("2024-06-03")


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


# --- batch split + no-lookahead guard -----------------------------------------


def _utc(year, month, day, hour) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def test_split_batch_multiindex() -> None:
    columns = pd.MultiIndex.from_product([["AAPL", "MSFT"], ["Open", "Close"]])
    data = pd.DataFrame([[1, 2, 3, 4], [5, 6, 7, 8]], columns=columns)
    out = _split_batch(data, ["AAPL", "MSFT"])
    assert set(out) == {"AAPL", "MSFT"}
    assert list(out["AAPL"].columns) == ["Open", "Close"]


def test_split_batch_drops_all_nan_ticker() -> None:
    # A symbol that returned nothing comes back as all-NaN columns -> dropped.
    columns = pd.MultiIndex.from_product([["AAPL", "DEAD"], ["Open", "Close"]])
    data = pd.DataFrame([[1, 2, None, None]], columns=columns)
    out = _split_batch(data, ["AAPL", "DEAD"])
    assert set(out) == {"AAPL"}


def test_split_batch_single_ticker_flat_columns() -> None:
    data = pd.DataFrame({"Open": [1.0], "Close": [1.5]})
    out = _split_batch(data, ["AAPL"])
    assert list(out) == ["AAPL"]


def test_extract_splits_keeps_only_nonzero() -> None:
    frame = pd.DataFrame(
        {"Stock Splits": [0.0, 2.0, 0.0]},
        index=pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
    )
    splits = _extract_splits(frame)
    assert list(splits) == [2.0]


def test_extract_splits_absent_column_is_empty() -> None:
    assert _extract_splits(pd.DataFrame({"open": [1.0]})).empty


def test_daily_bar_incomplete_only_during_session() -> None:
    today = date(2024, 6, 5)  # a Wednesday
    # Same-day bar before the close cutoff is partial; after it is complete.
    assert _last_bar_is_incomplete(today, "daily", _utc(2024, 6, 5, 15)) is True
    assert _last_bar_is_incomplete(today, "daily", _utc(2024, 6, 5, 22)) is False
    # A prior day's bar is always complete.
    assert _last_bar_is_incomplete(date(2024, 6, 4), "daily", _utc(2024, 6, 5, 15)) is False


def test_weekly_bar_incomplete_until_friday_close() -> None:
    monday = date(2024, 6, 3)  # bar dated to the start of that ISO week
    # Mid-week the weekly bar is still forming...
    assert _last_bar_is_incomplete(monday, "weekly", _utc(2024, 6, 5, 12)) is True
    # ...and after Friday's close (Saturday run) it's complete.
    assert _last_bar_is_incomplete(monday, "weekly", _utc(2024, 6, 8, 14)) is False
    # A prior week's bar is always complete.
    assert _last_bar_is_incomplete(date(2024, 5, 27), "weekly", _utc(2024, 6, 5, 12)) is False


def test_expected_sessions_daily_excludes_holiday() -> None:
    # The window spans July 4 (a US market holiday) -> it must not be an expected session.
    idx = pd.DatetimeIndex(["2024-07-01", "2024-07-08"])
    sessions = _expected_sessions("daily", idx)
    assert sessions is not None
    assert pd.Timestamp("2024-07-04") not in set(sessions)  # holiday
    assert pd.Timestamp("2024-07-05") in set(sessions)  # regular session


def test_expected_sessions_weekly_is_none() -> None:
    idx = pd.DatetimeIndex(["2024-07-01", "2024-07-08"])
    assert _expected_sessions("weekly", idx) is None


def test_slice_sessions_restricts_to_frame_span() -> None:
    master = pd.DatetimeIndex(pd.bdate_range("2024-06-01", "2024-06-30"))
    out = _slice_sessions(master, pd.DatetimeIndex(["2024-06-10", "2024-06-20"]))
    assert out is not None
    assert out[0] >= pd.Timestamp("2024-06-10") and out[-1] <= pd.Timestamp("2024-06-20")


def test_drop_incomplete_last_bar_removes_partial() -> None:
    df = pd.DataFrame(
        {"close": [1.0, 2.0]},
        index=pd.to_datetime(["2024-06-04", "2024-06-05"]),
    )
    # Intraday on 2024-06-05 -> the trailing bar is partial and dropped.
    trimmed = _drop_incomplete_last_bar(df, "daily", _utc(2024, 6, 5, 15))
    assert len(trimmed) == 1 and trimmed.index[-1] == pd.Timestamp("2024-06-04")
    # After the close -> nothing dropped.
    kept = _drop_incomplete_last_bar(df, "daily", _utc(2024, 6, 5, 22))
    assert len(kept) == 2
