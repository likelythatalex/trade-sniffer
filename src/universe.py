"""Build and filter the ticker universe (SPEC §4.1, §6.5).

Loads bare tickers from ``universe.txt`` and applies the liquidity gate at scan
time. The liquidity gate is the one **intentional absolute threshold** in the
system (a universe-eligibility floor, not a signal threshold): volume analysis is
meaningless on illiquid names. SPY is exempt — it's a reference series, not a
candidate — and the caller skips the gate for it.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .config import LiquidityConfig

# SPEC §6.5: "20-day average dollar volume". Fixed by the methodology, not a tunable.
_LIQUIDITY_LOOKBACK = 20


def load_universe(path: Path) -> list[str]:
    """Read bare tickers from ``universe.txt`` (one per line).

    Blank lines and ``#`` comments are skipped; tickers are upper-cased and
    de-duplicated while preserving first-seen order.
    """
    seen: dict[str, None] = {}  # dict preserves insertion order; values unused
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        seen.setdefault(line.upper(), None)
    return list(seen)


def passes_liquidity_gate(df: pd.DataFrame, liquidity: LiquidityConfig) -> tuple[bool, str | None]:
    """Apply the absolute liquidity floor (§6.5) to a ticker's (daily) bars.

    Returns ``(True, None)`` if it passes, else ``(False, reason)`` for the
    skipped-with-reason log. Checks last price and the 20-day average dollar volume
    (close × volume). Expects daily bars — the gate is a daily-liquidity concept.
    """
    if df.empty:
        return False, "no data for liquidity check"

    last_price = float(df["close"].iloc[-1])
    if last_price < liquidity.min_price:
        return False, f"price ${last_price:.2f} < ${liquidity.min_price:.2f} floor"

    recent = df.iloc[-_LIQUIDITY_LOOKBACK:]
    avg_dollar_volume = float((recent["close"] * recent["volume"]).mean())
    if avg_dollar_volume < liquidity.min_avg_dollar_volume:
        return (
            False,
            f"avg dollar-volume ${avg_dollar_volume:,.0f} < ${liquidity.min_avg_dollar_volume:,.0f} floor",
        )

    return True, None
