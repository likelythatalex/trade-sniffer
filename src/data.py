"""Fetch + cache market data via yfinance (SPEC §5.1).

The only module that does network I/O for OHLCV. Weekly bars are fetched
**natively** (``interval="1wk"``) — the daily lookback is too short to reconstruct
weekly history. Prices use ``auto_adjust=True`` so splits/dividends give a
continuous series (no fake gaps); ``data_quality`` keeps a split-mismatch check as a
safety net. The exchange prefix is read from the *same* history call's metadata (no
extra request), with ``symbol_overrides.csv`` taking precedence. SPY is always
fetched (for relative strength) and is exempt from the liquidity gate.

Per-ticker failures raise ``DataError``; the scanner catches and fail-soft skips.

Note on no-lookahead: the scheduled run happens after the close, so the last bar is
the last *closed* bar. The strategy always evaluates the final bar (SPEC §5.1).

Testing: the pure helpers below are unit-tested; the live fetch is exercised by a
manual run rather than committed network tests (keeps the suite hermetic).
"""
from __future__ import annotations

import pickle
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from .config import Config, required_history

_COLUMN_MAP = {"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}
_OHLCV = ["open", "high", "low", "close", "volume"]
_INTERVALS = {"daily": "1d", "weekly": "1wk"}


class DataError(Exception):
    """Raised when data for a ticker cannot be fetched or is unusable."""


@dataclass
class FetchResult:
    """OHLCV plus the metadata downstream stages need.

    Attributes:
        df: OHLCV frame (lowercase columns) indexed by bar timestamp.
        exchange: resolved exchange for the TV symbol; ``None`` if unresolved.
        corporate_actions: splits Series (date -> ratio), passed into
            ``data_quality`` so that module stays pure.
    """

    df: pd.DataFrame
    exchange: str | None
    corporate_actions: pd.Series


def fetch_ohlcv(
    ticker: str, timeframe: str, config: Config, today: date | None = None
) -> FetchResult:
    """Fetch (or read from cache) OHLCV + splits + exchange for one ticker/timeframe.

    The fetch window comes from the configured lookback (validated to cover
    ``required_history``). Results are cached per ticker+timeframe+date so repeated
    fetches within a run — and same-day re-runs — don't re-pull.
    """
    today = today or date.today()
    cache_file = _cache_path(config.data.cache_dir, ticker, timeframe, today)
    cached = _read_cache(cache_file)
    if cached is not None:
        return cached

    raw, metadata, splits = _download(ticker, timeframe, config, today)
    if raw is None or raw.empty:
        raise DataError(f"no data returned for {ticker} [{timeframe}]")

    overrides = _load_overrides(Path(config.symbols.override_map_file))
    result = FetchResult(
        df=_normalize_columns(raw, ticker),
        exchange=_resolve_exchange(metadata, ticker, overrides),
        corporate_actions=splits if splits is not None else pd.Series(dtype=float),
    )
    _write_cache(cache_file, result)
    return result


def fetch_spy(timeframe: str, config: Config, today: date | None = None) -> pd.DataFrame:
    """Fetch SPY for relative-strength (§7.1). Always fetched; gate-exempt."""
    return fetch_ohlcv("SPY", timeframe, config, today).df


# --- Network boundary (the only impure part; isolated for easy mocking) -------


def _download(
    ticker: str, timeframe: str, config: Config, today: date
) -> tuple[pd.DataFrame | None, dict[str, Any], pd.Series | None]:
    """Pull raw history + metadata + splits from yfinance. Raises ``DataError``."""
    try:
        import yfinance as yf

        handle = yf.Ticker(ticker)
        start = _fetch_window(timeframe, config, today)
        raw = handle.history(start=start.isoformat(), interval=_interval(timeframe), auto_adjust=True)
        metadata = getattr(handle, "history_metadata", {}) or {}
        splits = getattr(handle, "splits", None)
        return raw, metadata, splits
    except DataError:
        raise
    except Exception as exc:  # network/parse/delisting -> uniform DataError
        raise DataError(f"fetch failed for {ticker} [{timeframe}]: {exc}") from exc


# --- Pure helpers (unit-tested) -----------------------------------------------


def _interval(timeframe: str) -> str:
    try:
        return _INTERVALS[timeframe]
    except KeyError as exc:
        raise DataError(f"unsupported timeframe '{timeframe}'.") from exc


def _fetch_window(timeframe: str, config: Config, today: date) -> date:
    """Start date for the fetch, from the configured lookback (covers warmup).

    ``required_history`` is asserted in config validation; we read it here only to
    fail loud if a caller bypassed that path.
    """
    if timeframe == "daily":
        lookback_days = config.data.daily_lookback_days
    elif timeframe == "weekly":
        lookback_days = config.data.weekly_lookback_weeks * 7
    else:
        raise DataError(f"unsupported timeframe '{timeframe}'.")
    if lookback_days < required_history(config, timeframe):
        raise DataError(f"{timeframe} lookback does not cover required history.")
    return today - timedelta(days=lookback_days)


def _normalize_columns(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Rename yfinance's capitalized OHLCV to the internal lowercase schema.

    Decouples the pipeline from the data source's naming (so yfinance's column
    casing never leaks downstream).
    """
    missing = [col for col in _COLUMN_MAP if col not in raw.columns]
    if missing:
        raise DataError(f"{ticker}: data missing expected column(s) {missing}.")
    return raw.rename(columns=_COLUMN_MAP)[_OHLCV].copy()


def _resolve_exchange(metadata: dict[str, Any], ticker: str, overrides: dict[str, str]) -> str | None:
    """Override map wins; else the exchange from the history metadata; else ``None``."""
    if ticker in overrides:
        return overrides[ticker]
    return metadata.get("fullExchangeName") or metadata.get("exchangeName") or None


def _load_overrides(path: Path) -> dict[str, str]:
    """Read ``symbol_overrides.csv`` (ticker,exchange) into a dict; skip comments/header."""
    if not path.exists():
        return {}
    overrides: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 2 or parts[0].lower() == "ticker":
            continue
        overrides[parts[0]] = parts[1]
    return overrides


def _cache_path(cache_dir: str, ticker: str, timeframe: str, day: date) -> Path:
    safe_ticker = ticker.replace("/", "_").replace(":", "_")
    return Path(cache_dir) / f"{safe_ticker}_{timeframe}_{day.isoformat()}.pkl"


def _read_cache(path: Path) -> FetchResult | None:
    if not path.exists():
        return None
    try:
        with path.open("rb") as handle:
            return pickle.load(handle)
    except Exception:  # corrupt/old cache -> ignore and re-fetch
        return None


def _write_cache(path: Path, result: FetchResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        pickle.dump(result, handle)
