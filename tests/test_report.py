"""Tests for report.py — TV symbol mapping, signals.csv, TV import file, HTML render.

The HTML render is checked for the must-have elements (cards, scores, the TV embed
script, attribution) rather than exact markup, so styling changes don't shatter it.
"""
from __future__ import annotations

import csv
import dataclasses
from datetime import date
from pathlib import Path

import pytest

from src import config as config_module
from src import report

CONFIG = config_module.load_config(Path("config.yaml"))

def _chart(range_high=110.0, range_low=100.0, markers=None):
    return {
        "candles": [{"time": "2024-05-31", "open": 104.0, "high": 106.0, "low": 103.0, "close": 105.0}],
        "volume": [{"time": "2024-05-31", "value": 1000.0, "up": True}],
        "range_high": range_high, "range_low": range_low, "markers": markers or [],
    }


CARDS = [
    {
        "ticker": "XOM",
        "exchange": "NYSE",
        "direction": "accumulation",
        "score": 72.0,
        "sub_scores": {"volume_behavior": 80.0, "range_structure": 40.0},
        "reasons": ["spring at support"],
        "chart": _chart(markers=[{"time": "2024-05-31", "type": "spring"}]),
        "review": {"text": "Verdict: aligned\nClean base.", "verdict": "aligned", "model": "claude-haiku-4-5-20251001"},
    },
    {
        "ticker": "AAPL",
        "exchange": "NasdaqGS",
        "direction": "accumulation",
        "score": 55.0,
        "sub_scores": {"volume_behavior": 50.0},
        "reasons": ["no supply"],
        "chart": _chart(),
    },
    {
        "ticker": "KO",
        "exchange": "NYSE",
        "direction": "distribution",
        "score": 65.0,
        "sub_scores": {"volume_behavior": -60.0},
        "reasons": ["upthrust at resistance"],
        "chart": _chart(),
    },
]


def config_with_output(tmp_path: Path) -> config_module.Config:
    out = dataclasses.replace(CONFIG.output, dir=str(tmp_path))
    return dataclasses.replace(CONFIG, output=out)


def test_to_tv_symbol_maps_exchanges() -> None:
    assert report._to_tv_symbol("AAPL", "NasdaqGS") == "NASDAQ:AAPL"
    assert report._to_tv_symbol("XOM", "NYSE") == "NYSE:XOM"
    assert report._to_tv_symbol("FOO", None) == "FOO"  # bare fallback
    assert report._to_tv_symbol("FOO", "WeirdExchange") == "FOO"


def test_append_signals_writes_header_once(tmp_path: Path) -> None:
    path = tmp_path / "signals.csv"
    row = {col: "" for col in report.SIGNALS_COLUMNS}
    row.update({"ticker": "XOM", "timeframe": "daily", "composite_score": 72.0, "transition": "new"})

    report.append_signals([row], path)
    report.append_signals([row], path)  # second run must not re-write the header

    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == list(report.SIGNALS_COLUMNS)  # one header
    assert len(rows) == 3  # header + 2 data rows
    assert rows[0].count("ticker") == 1


def test_append_signals_migrates_older_schema(tmp_path: Path) -> None:
    # An existing log written before close/volume existed must be upgraded in place,
    # not corrupted: old rows keep their data, new columns back-fill blank, header aligns.
    path = tmp_path / "signals.csv"
    old_cols = [c for c in report.SIGNALS_COLUMNS if c not in ("close", "volume")]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=old_cols)
        writer.writeheader()
        writer.writerow({**{c: "" for c in old_cols}, "ticker": "OLD", "composite_score": 50.0})

    new_row = {col: "" for col in report.SIGNALS_COLUMNS}
    new_row.update({"ticker": "NEW", "close": 101.5, "volume": 1234567})
    report.append_signals([new_row], path)

    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == list(report.SIGNALS_COLUMNS)  # header upgraded to current schema
    data = {r[1]: r for r in rows[1:]}  # keyed by ticker (col 1)
    close_idx = list(report.SIGNALS_COLUMNS).index("close")
    assert data["OLD"][close_idx] == ""          # migrated old row: new column back-filled blank
    assert data["NEW"][close_idx] == "101.5"     # new row aligned under the new header


def test_append_market_writes_header_once(tmp_path: Path) -> None:
    path = tmp_path / "market.csv"
    row = {"run_ts": "2024-06-01T22:00:00Z", "timeframe": "daily", "regime": "risk-on",
           "spy_above_ma": True, "spy_distance_pct": 3.2, "breadth_pct": 61.0,
           "n_breadth": 500, "ma_window": 200}
    report.append_market(row, path)
    report.append_market({**row, "regime": "neutral"}, path)  # 2nd run -> no new header

    rows = list(csv.reader(path.read_text(encoding="utf-8").splitlines()))
    assert rows[0] == list(report.MARKET_COLUMNS)
    assert len(rows) == 3  # header + 2 rows
    assert rows[1][report.MARKET_COLUMNS.index("regime")] == "risk-on"


def test_write_tv_import_file(tmp_path: Path) -> None:
    path = report.write_tv_import_file(CARDS, "daily", config_with_output(tmp_path))
    text = path.read_text(encoding="utf-8")
    assert path.name == "watchlist_daily.txt"
    assert "###ACCUMULATION" in text and "###DISTRIBUTION" in text
    assert "NYSE:XOM" in text and "NASDAQ:AAPL" in text and "NYSE:KO" in text


def test_write_index_page_links_both_timeframes(tmp_path: Path) -> None:
    path = report.write_index_page(config_with_output(tmp_path))
    assert path.name == "index.html"
    html = path.read_text(encoding="utf-8")
    assert 'href="latest_daily.html"' in html
    assert 'href="latest_weekly.html"' in html


def test_render_dashboard_writes_files_with_required_content(tmp_path: Path) -> None:
    cfg = config_with_output(tmp_path)
    summary = {"scanned": 10, "flagged": 3, "skipped": 1, "errored": 0}
    path = report.render_dashboard(CARDS, "daily", cfg, today=date(2024, 6, 1), summary=summary)

    assert path.name == "report_daily_2024-06-01.html"
    assert (Path(cfg.output.dir) / "latest_daily.html").exists()  # latest copy refreshed

    html = path.read_text(encoding="utf-8")
    assert "XOM" in html and "KO" in html
    assert "NYSE:XOM" in html  # TV symbol embedded (for the open-in-TradingView link)
    assert "lightweight-charts" in html  # the Lightweight Charts library
    assert "candles" in html and "range_high" in html  # OHLCV + annotation data embedded
    assert "TradingView" in html  # attribution kept visible
    assert "Accumulation" in html and "Distribution" in html
    assert "Clean base." in html  # the agent review is embedded for display


def test_render_dashboard_ranks_within_direction(tmp_path: Path) -> None:
    cfg = config_with_output(tmp_path)
    html = report.render_dashboard(CARDS, "daily", cfg, today=date(2024, 6, 1)).read_text(encoding="utf-8")
    # XOM (72) must appear before AAPL (55) — ranked descending within accumulation.
    assert html.index("XOM") < html.index("AAPL")


def test_render_dashboard_shows_failed_setups(tmp_path: Path) -> None:
    cfg = config_with_output(tmp_path)
    failed = [
        {"ticker": "TSLA", "direction": "accumulation", "prior_score": 74.0, "current_score": 58.0},
        {"ticker": "GE", "direction": "distribution", "prior_score": 71.0, "current_score": None},
    ]
    html = report.render_dashboard(
        CARDS, "daily", cfg, today=date(2024, 6, 1), failed=failed
    ).read_text(encoding="utf-8")

    assert "Recently invalidated (2)" in html
    assert "TSLA" in html and "74 → 58" in html  # prior -> current drop shown
    assert "GE" in html and "71 → n/a" in html   # not evaluated this run -> n/a


def test_render_dashboard_omits_failed_section_when_none(tmp_path: Path) -> None:
    cfg = config_with_output(tmp_path)
    html = report.render_dashboard(CARDS, "daily", cfg, today=date(2024, 6, 1)).read_text(encoding="utf-8")
    assert "Recently invalidated" not in html


def test_render_dashboard_has_filter_toolbar(tmp_path: Path) -> None:
    cfg = config_with_output(tmp_path)
    html = report.render_dashboard(CARDS, "daily", cfg, today=date(2024, 6, 1)).read_text(encoding="utf-8")
    assert 'id="q"' in html and 'id="sort"' in html       # search box + sort control
    assert 'id="rows-acc"' in html and 'id="rows-dist"' in html  # sortable row containers
    assert 'data-score="72.00"' in html                   # rows carry score for client-side sort


def test_render_dashboard_shows_episode_badge(tmp_path: Path) -> None:
    cfg = config_with_output(tmp_path)
    card = {**CARDS[0], "prior_episode_count": 2,
            "episode_history": "Flagged 2 times before on this timeframe."}
    failed = [{"ticker": "TSLA", "direction": "accumulation", "prior_score": 74.0,
               "current_score": 58.0, "prior_episode_count": 3}]
    html = report.render_dashboard(
        [card], "daily", cfg, today=date(2024, 6, 1), failed=failed
    ).read_text(encoding="utf-8")

    assert "↻2" in html  # re-flag badge on the candidate row
    assert "↻3" in html  # and on the invalidated entry
    assert "Flagged 2 times before" in html  # history text embedded for the panel
