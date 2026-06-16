"""Fetch + cache market data via yfinance (SPEC §5.1).

The only module that does network I/O for OHLCV. Weekly bars are fetched
**natively** (``interval="1wk"``) — the daily lookback is too short to reconstruct
weekly history. This module also resolves the exchange prefix for TV symbols,
fetches corporate actions (passed to the pure ``data_quality`` step), and always
fetches SPY for the relative-strength confirmation.

No-lookahead rule: only bars up to and including the last *closed* bar are used.
Per-ticker failures are logged and skipped, never raised (fail soft).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass
class FetchResult:
    """OHLCV plus the metadata downstream stages need.

    Attributes:
        df: OHLCV frame indexed by bar timestamp (last closed bar only).
        exchange: resolved exchange (e.g. ``"NASDAQ"``) for the TV symbol; ``None``
            if unresolved (the ticker is then skipped-with-reason).
        corporate_actions: splits/dividends, passed into ``data_quality`` so that
            module stays pure.
    """

    df: pd.DataFrame
    exchange: str | None
    corporate_actions: pd.DataFrame


def fetch_ohlcv(ticker: str, timeframe: str, config: Any) -> FetchResult:
    """Fetch (or read from cache) OHLCV + corporate actions for one ticker/timeframe.

    Honors the warmup rule: pulls enough history that the earliest scored bar has a
    full feature baseline (lookback >= scoring_window + features.baseline_window).
    """
    raise NotImplementedError


def fetch_spy(timeframe: str, config: Any) -> pd.DataFrame:
    """Fetch SPY for relative-strength (§7.1). Always fetched; exempt from the
    liquidity gate."""
    raise NotImplementedError


def resolve_exchange_prefix(ticker: str, config: Any) -> str | None:
    """Resolve the exchange (yfinance metadata, with the optional override map).
    Returns ``None`` if unresolvable — caller skips the ticker with a reason."""
    raise NotImplementedError
