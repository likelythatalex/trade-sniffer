"""Tests for scripts/build_universe.py — the manual-block preservation logic (pure, no network).

The script lives under scripts/ (not a package), so we load it by path via importlib. Only the
pure compose/parse helpers are exercised; the Wikipedia fetch is never called.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "build_universe.py"
_spec = importlib.util.spec_from_file_location("build_universe", _SCRIPT)
bu = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bu)

_EXISTING = """# header line
# more header

AAPL
MSFT
ZTS

# --- Manual additions (NOT from the S&P 500 / Nasdaq-100 generator) ---
# Preserved across re-runs.
# Nuclear / uranium names:
CCJ
LEU
OKLO
"""


def test_manual_block_extracted_from_marker_to_eof() -> None:
    block = bu._manual_block(_EXISTING)
    assert block.startswith("# --- Manual additions")
    assert bu._tickers_in(block) == {"CCJ", "LEU", "OKLO"}  # tickers only, comments excluded


def test_no_marker_means_no_manual_block() -> None:
    assert bu._manual_block("AAPL\nMSFT\n") == ""
    assert bu._tickers_in("") == set()


def test_compose_preserves_manual_block_verbatim() -> None:
    text = bu.compose_universe(["AAPL", "MSFT"], _EXISTING)
    assert text.startswith(bu._HEADER)                       # regenerated header
    assert "\n# --- Manual additions" in text               # manual block carried over
    assert "CCJ\nLEU\nOKLO" in text                          # its tickers preserved
    assert "# Nuclear / uranium names:" in text              # and its comments


def test_compose_dedupes_a_graduated_manual_ticker() -> None:
    # OKLO is in BOTH the freshly generated index list and the manual block -> appears once.
    text = bu.compose_universe(["AAPL", "OKLO", "MSFT"], _EXISTING)
    assert text.count("OKLO") == 1                           # not duplicated
    generated_section = text.split("# --- Manual additions")[0]
    assert "OKLO" not in generated_section                   # excluded from the generated set
    assert "AAPL" in generated_section and "MSFT" in generated_section


def test_compose_without_existing_manual_block_just_writes_generated() -> None:
    text = bu.compose_universe(["MSFT", "AAPL", "NAN", ""], "")
    assert text == bu._HEADER + "AAPL\nMSFT\n"               # sorted, NAN/empty dropped, no block
