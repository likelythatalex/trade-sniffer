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

CARDS = [
    {
        "ticker": "XOM",
        "exchange": "NYSE",
        "direction": "accumulation",
        "score": 72.0,
        "sub_scores": {"volume_behavior": 80.0, "range_structure": 40.0},
        "reasons": ["spring at support"],
    },
    {
        "ticker": "AAPL",
        "exchange": "NasdaqGS",
        "direction": "accumulation",
        "score": 55.0,
        "sub_scores": {"volume_behavior": 50.0},
        "reasons": ["no supply"],
    },
    {
        "ticker": "KO",
        "exchange": "NYSE",
        "direction": "distribution",
        "score": 65.0,
        "sub_scores": {"volume_behavior": -60.0},
        "reasons": ["upthrust at resistance"],
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


def test_write_tv_import_file(tmp_path: Path) -> None:
    path = report.write_tv_import_file(CARDS, "daily", config_with_output(tmp_path))
    text = path.read_text(encoding="utf-8")
    assert path.name == "watchlist_daily.txt"
    assert "###ACCUMULATION" in text and "###DISTRIBUTION" in text
    assert "NYSE:XOM" in text and "NASDAQ:AAPL" in text and "NYSE:KO" in text


def test_render_dashboard_writes_files_with_required_content(tmp_path: Path) -> None:
    cfg = config_with_output(tmp_path)
    summary = {"scanned": 10, "flagged": 3, "skipped": 1, "errored": 0}
    path = report.render_dashboard(CARDS, "daily", cfg, today=date(2024, 6, 1), summary=summary)

    assert path.name == "report_daily_2024-06-01.html"
    assert (Path(cfg.output.dir) / "latest_daily.html").exists()  # latest copy refreshed

    html = path.read_text(encoding="utf-8")
    assert "XOM" in html and "KO" in html
    assert "NYSE:XOM" in html  # TV symbol embedded
    assert "embed-widget-advanced-chart.js" in html  # the TV widget
    assert "TradingView" in html  # attribution kept visible
    assert "Accumulation" in html and "Distribution" in html
    # Resizable / expandable chart controls.
    assert "autosize" in html  # widget fills its container (so resize/fullscreen work)
    assert "class=\"expand\"" in html and "fullscreen" in html
    assert "chart-close" in html  # visible exit control in fullscreen


def test_render_dashboard_ranks_within_direction(tmp_path: Path) -> None:
    cfg = config_with_output(tmp_path)
    html = report.render_dashboard(CARDS, "daily", cfg, today=date(2024, 6, 1)).read_text(encoding="utf-8")
    # XOM (72) must appear before AAPL (55) — ranked descending within accumulation.
    assert html.index("XOM") < html.index("AAPL")
