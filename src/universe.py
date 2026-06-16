"""Build and filter the ticker universe (SPEC §4.1, §6.5).

Loads bare tickers from ``universe.txt`` and applies the liquidity gate at scan
time. The liquidity gate is the one **intentional absolute threshold** in the
system (a universe-eligibility floor, not a signal threshold): volume analysis is
meaningless on illiquid names. SPY is exempt — it's a reference series, not a
candidate.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


def load_universe(path: Path) -> list[str]:
    """Read bare tickers from ``universe.txt`` (one per line; blanks/comments skipped)."""
    raise NotImplementedError


def passes_liquidity_gate(df: pd.DataFrame, liquidity_cfg: Any) -> tuple[bool, str | None]:
    """Apply the absolute liquidity floor (§6.5).

    Returns ``(True, None)`` if the ticker passes, else ``(False, reason)`` for the
    skipped-with-reason log. Checks 20-day avg dollar volume and minimum price.
    """
    raise NotImplementedError
