"""Tests for the private trade journal (SPEC §8A.2) — the data layer (Step 4).

Hermetic: a tmp_path journal.csv, no network/AI. Asserts add/list/close behavior, id
assignment, direction normalization, validation, and tolerance of a missing file.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from src.journal import (
    JournalError,
    add_trade,
    close_trade,
    evaluate_entries,
    list_trades,
    load_entries,
)


def journal(tmp_path: Path) -> Path:
    return tmp_path / "journal.csv"


def _add_long(path: Path, ticker: str = "XOM", **overrides):
    args = dict(ticker=ticker, direction="long", entry=110.0, stop=101.0, target=120.0, size=90.0)
    args.update(overrides)
    return add_trade(path, today=date(2024, 6, 1), **args)


# --- add ----------------------------------------------------------------------


def test_add_creates_open_trade_with_id_one(tmp_path: Path) -> None:
    row = _add_long(journal(tmp_path))
    assert row["id"] == 1
    assert row["status"] == "open"
    assert row["ticker"] == "XOM" and row["direction"] == "long"
    assert row["opened_date"] == "2024-06-01"
    assert row["exit_price"] == ""  # open -> no exit yet


def test_ids_increment(tmp_path: Path) -> None:
    path = journal(tmp_path)
    assert _add_long(path, "AAA")["id"] == 1
    assert _add_long(path, "BBB")["id"] == 2
    assert _add_long(path, "CCC")["id"] == 3


def test_direction_aliases_normalize(tmp_path: Path) -> None:
    path = journal(tmp_path)
    # Signal terms map onto trade directions for convenience.
    assert add_trade(path, ticker="KO", direction="distribution", entry=70.0, stop=75.0, target=60.0, size=50.0)["direction"] == "short"
    assert add_trade(path, ticker="MS", direction="accumulation", entry=80.0, stop=72.0, target=95.0, size=40.0)["direction"] == "long"


def test_add_rejects_bad_direction(tmp_path: Path) -> None:
    with pytest.raises(JournalError, match="direction"):
        add_trade(journal(tmp_path), ticker="X", direction="sideways", entry=10.0, stop=9.0, target=12.0, size=1.0)


def test_add_rejects_nonpositive_numbers(tmp_path: Path) -> None:
    with pytest.raises(JournalError, match="stop"):
        add_trade(journal(tmp_path), ticker="X", direction="long", entry=10.0, stop=0.0, target=12.0, size=1.0)


# --- list ---------------------------------------------------------------------


def test_list_filters_by_status(tmp_path: Path) -> None:
    path = journal(tmp_path)
    _add_long(path, "AAA")
    _add_long(path, "BBB")
    close_trade(path, 2, exit_price=118.0, exit_date=date(2024, 6, 10))
    assert {e["ticker"] for e in list_trades(path, "open")} == {"AAA"}
    assert {e["ticker"] for e in list_trades(path, "closed")} == {"BBB"}
    assert len(list_trades(path)) == 2


def test_load_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_entries(tmp_path / "nope.csv") == []


# --- close --------------------------------------------------------------------


def test_close_sets_exit_and_status(tmp_path: Path) -> None:
    path = journal(tmp_path)
    _add_long(path)
    closed = close_trade(path, 1, exit_price=118.5, exit_date=date(2024, 6, 12), notes="hit near target")
    assert closed["status"] == "closed"
    assert float(closed["exit_price"]) == 118.5
    assert closed["exit_date"] == "2024-06-12"
    assert closed["notes"] == "hit near target"
    # Persisted across a reload.
    assert load_entries(path)[0]["status"] == "closed"


def test_close_unknown_id_raises(tmp_path: Path) -> None:
    with pytest.raises(JournalError, match="no trade with id 99"):
        close_trade(journal(tmp_path), 99, exit_price=100.0)


def test_close_already_closed_raises(tmp_path: Path) -> None:
    path = journal(tmp_path)
    _add_long(path)
    close_trade(path, 1, exit_price=118.0)
    with pytest.raises(JournalError, match="already closed"):
        close_trade(path, 1, exit_price=119.0)


def test_evaluate_entries_pairs_trades_with_outcomes(tmp_path: Path) -> None:
    path = journal(tmp_path)
    # Long XOM opened 2024-06-01, entry 110 / stop 101 / target 120.
    _add_long(path, "XOM")
    # Forward bars AFTER 2024-06-01: a later bar tags the target (high 121).
    idx = pd.to_datetime(["2024-06-01", "2024-06-02", "2024-06-03"])
    prices = {"XOM": pd.DataFrame({"high": [112, 115, 121], "low": [108, 109, 113], "close": [111, 114, 120]}, index=idx)}
    (entry, outcome), = evaluate_entries(load_entries(path), prices)
    assert entry["ticker"] == "XOM"
    assert outcome is not None and outcome.resolution == "target"
    # The 2024-06-01 entry bar is excluded (forward = strictly after opened_date).
    assert outcome.bars_held == 2


def test_evaluate_entries_handles_missing_prices(tmp_path: Path) -> None:
    path = journal(tmp_path)
    _add_long(path, "XOM")
    (_, outcome), = evaluate_entries(load_entries(path), {})  # no price data for XOM
    assert outcome is None


def test_round_trip_preserves_fields(tmp_path: Path) -> None:
    path = journal(tmp_path)
    _add_long(path, "XOM", source="wyckoff daily 2024-06-01", timeframe="daily")
    reloaded = load_entries(path)[0]
    assert reloaded["source"] == "wyckoff daily 2024-06-01"
    assert reloaded["timeframe"] == "daily"
    assert float(reloaded["entry"]) == 110.0
