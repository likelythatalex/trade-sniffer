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
    build_trade_review_prompt,
    close_trade,
    evaluate_entries,
    list_trades,
    load_entries,
    render_journal_html,
    review_closed_trades,
)
from src.review import Reviewer
from src.trade_outcome import TradeOutcome


class StubReviewer(Reviewer):
    """Counts calls so we can assert the post-trade cost controls fire (no network)."""

    def __init__(self) -> None:
        self.calls = 0

    def review(self, prompt: str) -> dict[str, str]:
        self.calls += 1
        return {"text": "Process: good\nFollowed the plan.", "verdict": "good"}


def _closed(ticker: str, trade_id: int) -> dict:
    return {
        "id": trade_id, "opened_date": "2024-06-01", "ticker": ticker, "direction": "long",
        "entry": "110", "stop": "101", "target": "120", "size": "90", "status": "closed",
        "exit_date": "2024-06-10", "exit_price": "118", "source": "", "notes": "",
    }


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


def test_review_only_closed_trades_capped_and_cached() -> None:
    outcome = TradeOutcome(resolution="target", realized_r=1.1, mfe_r=1.3, mae_r=0.4, bars_held=6)
    paired = [
        (_closed("AAA", 1), outcome),
        (_closed("BBB", 2), outcome),
        ({**_closed("CCC", 3), "status": "open"}, None),  # open -> never reviewed
    ]
    stub = StubReviewer()
    cache = review_closed_trades(paired, stub, cache={}, max_reviews=5)
    assert stub.calls == 2  # only the two CLOSED trades
    assert set(cache) == {"1", "2"} and cache["1"]["ticker"] == "AAA"

    # Re-run with the cache populated: nothing re-spent.
    again = review_closed_trades(paired, stub, cache=cache, max_reviews=5)
    assert stub.calls == 2  # unchanged
    assert set(again) == {"1", "2"}


def test_review_respects_per_run_cap() -> None:
    paired = [(_closed(f"T{i}", i), None) for i in range(5)]
    stub = StubReviewer()
    cache = review_closed_trades(paired, stub, cache={}, max_reviews=2)
    assert stub.calls == 2 and len(cache) == 2  # hard cap honored


def test_build_trade_review_prompt_includes_evidence() -> None:
    outcome = TradeOutcome(resolution="stop", realized_r=-1.0, mfe_r=0.5, mae_r=1.2, bars_held=4)
    prompt = build_trade_review_prompt(_closed("XOM", 1), outcome)
    assert "XOM" in prompt and "long" in prompt
    assert "planned R:R" in prompt
    assert "actual" in prompt and "+0.89R" in prompt  # (118-110)/9 from the recorded exit
    assert "stop after 4 bars" in prompt


def test_render_journal_html_contains_trade_outcome_and_review(tmp_path: Path) -> None:
    outcome = TradeOutcome(resolution="target", realized_r=1.11, mfe_r=1.3, mae_r=0.4, bars_held=6)
    reviews = {"1": {"text": "Process: good\nClean execution.", "verdict": "good"}}
    out = render_journal_html([(_closed("XOM", 1), outcome)], reviews, tmp_path / "j.html")
    html = out.read_text(encoding="utf-8")
    assert "XOM" in html and "PRIVATE" in html  # private banner present
    assert "+0.89R" in html                      # actual realized R: (118-110)/9 from recorded exit
    assert "target after 6 bars" in html         # path outcome
    assert "Clean execution." in html and "process: good" in html  # reflection rendered


def test_render_journal_html_summary_excludes_open_from_win_rate(tmp_path: Path) -> None:
    win = _closed("AAA", 1)                                    # exit 118 -> +0.89R (win)
    loss = {**_closed("BBB", 2), "exit_price": "104"}          # exit 104 -> -0.67R (loss)
    open_trade = {**_closed("CCC", 3), "status": "open", "exit_price": ""}
    out = render_journal_html([(win, None), (loss, None), (open_trade, None)], {}, tmp_path / "j.html")
    html = out.read_text(encoding="utf-8")
    assert "50.0%" in html  # 2 closed, 1 win -> 50% (the open trade is excluded)


def test_render_journal_html_empty(tmp_path: Path) -> None:
    out = render_journal_html([], {}, tmp_path / "j.html")
    assert "No trades yet" in out.read_text(encoding="utf-8")


def test_round_trip_preserves_fields(tmp_path: Path) -> None:
    path = journal(tmp_path)
    _add_long(path, "XOM", source="wyckoff daily 2024-06-01", timeframe="daily")
    reloaded = load_entries(path)[0]
    assert reloaded["source"] == "wyckoff daily 2024-06-01"
    assert reloaded["timeframe"] == "daily"
    assert float(reloaded["entry"]) == 110.0
