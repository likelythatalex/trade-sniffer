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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .config import Config, required_history

_COLUMN_MAP = {"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}
_OHLCV = ["open", "high", "low", "close", "volume"]
_INTERVALS = {"daily": "1d", "weekly": "1wk"}

# No-lookahead guard. 16:00 ET (the US regular-session close) is 20:00 UTC under EDT
# and 21:00 UTC under EST; we use the later hour so a bar is only ever treated as
# "closed" once it's closed in *both* DST states. The cost is a ~1h window after the
# summer close where we conservatively fall back to the prior bar (safe, just stale).
# This avoids a timezone-database (tzdata) dependency on Windows.
_US_CLOSE_UTC_HOUR = 21


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
        expected_sessions: NYSE trading-session dates spanning the frame (daily only;
            ``None`` for weekly), passed into ``data_quality`` for missing-bar detection
            — computed here, at the I/O boundary, so the quality module stays pure.
    """

    df: pd.DataFrame
    exchange: str | None
    corporate_actions: pd.Series
    expected_sessions: pd.DatetimeIndex | None = None


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
    df = _drop_incomplete_last_bar(_normalize_columns(raw, ticker), timeframe, _now())
    if df.empty:
        raise DataError(f"{ticker} [{timeframe}]: no closed bars (only an in-session bar).")
    result = FetchResult(
        df=df,
        exchange=_resolve_exchange(metadata, ticker, overrides),
        corporate_actions=splits if splits is not None else pd.Series(dtype=float),
        expected_sessions=_expected_sessions(timeframe, df.index),
    )
    _write_cache(cache_file, result)
    return result


def fetch_many(
    tickers: list[str], timeframe: str, config: Config, today: date | None = None
) -> dict[str, FetchResult]:
    """Batch-fetch OHLCV (+splits) for many tickers in one threaded yfinance call.

    Far faster than per-ticker fetches at universe scale (network latency dominates).
    Exchange is **not** resolved here — it's only needed for tickers that reach the
    report, so ``resolve_exchange`` does it lazily for those few. Per-ticker results
    are cached, so cache hits skip the network and only the misses are downloaded; a
    ticker absent from the batch result simply gets no entry (the caller fail-soft
    skips it). A total download failure raises ``DataError`` (source-outage → abort,
    SPEC §10), rather than silently producing an empty report.
    """
    today = today or date.today()
    results: dict[str, FetchResult] = {}
    misses: list[str] = []
    for ticker in tickers:
        cached = _read_cache(_cache_path(config.data.cache_dir, ticker, timeframe, today))
        if cached is not None:
            results[ticker] = cached
        else:
            misses.append(ticker)

    if misses:
        downloaded = _download_many(misses, timeframe, config, today)
        now = _now()
        # Compute the session calendar ONCE for the whole batch (a schedule() call is
        # ~0.1s; per-ticker would cost ~a minute at universe scale), then slice per ticker.
        master_sessions = (
            _session_calendar(_fetch_window(timeframe, config, today), today)
            if timeframe == "daily"
            else None
        )
        for ticker in misses:
            frame = downloaded.get(ticker)
            if frame is None or frame.empty:
                continue  # no data for this ticker -> caller skips it
            try:
                df = _drop_incomplete_last_bar(_normalize_columns(frame, ticker), timeframe, now)
            except DataError:
                continue
            if df.empty:
                continue
            result = FetchResult(
                df=df,
                exchange=None,
                corporate_actions=_extract_splits(frame),
                expected_sessions=_slice_sessions(master_sessions, df.index),
            )
            _write_cache(_cache_path(config.data.cache_dir, ticker, timeframe, today), result)
            results[ticker] = result
    return results


def fetch_spy(timeframe: str, config: Config, today: date | None = None) -> pd.DataFrame:
    """Fetch SPY for relative-strength (§7.1). Always fetched; gate-exempt."""
    return fetch_ohlcv("SPY", timeframe, config, today).df


def resolve_exchange(ticker: str, config: Config) -> str | None:
    """Resolve the TradingView exchange prefix for one ticker (override map first,
    else a light metadata fetch). Only called for tickers that reach the report, so
    it's a handful of extra calls per run — not one per universe name. Failures →
    ``None`` (the report falls back to the bare ticker)."""
    overrides = _load_overrides(Path(config.symbols.override_map_file))
    if ticker in overrides:
        return overrides[ticker]
    try:
        import yfinance as yf

        handle = yf.Ticker(ticker)
        handle.history(period="5d", interval="1d")  # cheap call that populates metadata
        metadata = getattr(handle, "history_metadata", {}) or {}
    except Exception:
        return None
    return _resolve_exchange(metadata, ticker, overrides)


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


def _download_many(
    tickers: list[str], timeframe: str, config: Config, today: date
) -> dict[str, pd.DataFrame]:
    """Pull many symbols at once via ``yf.download`` (threaded), with splits/dividends
    (``actions=True``). Returns ``{ticker: per-ticker OHLCV+actions frame}``. Raises
    ``DataError`` only on a total failure of the batch call."""
    try:
        import yfinance as yf

        start = _fetch_window(timeframe, config, today)
        data = yf.download(
            tickers=tickers,
            start=start.isoformat(),
            interval=_interval(timeframe),
            auto_adjust=True,
            actions=True,
            group_by="ticker",
            threads=True,
            progress=False,
        )
    except DataError:
        raise
    except Exception as exc:
        raise DataError(f"batch fetch failed [{timeframe}]: {exc}") from exc
    return _split_batch(data, tickers)


# --- Pure helpers (unit-tested) -----------------------------------------------


def _now() -> datetime:
    """Current UTC time, isolated so the no-lookahead guard is easy to test."""
    return datetime.now(timezone.utc)


_NYSE_CALENDAR: Any = None


def _session_calendar(start: date, end: date) -> pd.DatetimeIndex:
    """NYSE trading-session dates in ``[start, end]`` (holiday/early-close aware), as a
    normalized DatetimeIndex. The calendar object is built once and reused."""
    global _NYSE_CALENDAR
    if _NYSE_CALENDAR is None:
        import pandas_market_calendars as mcal

        _NYSE_CALENDAR = mcal.get_calendar("NYSE")
    schedule = _NYSE_CALENDAR.schedule(start_date=pd.Timestamp(start), end_date=pd.Timestamp(end))
    return schedule.index.normalize()


def _expected_sessions(timeframe: str, index: pd.Index) -> pd.DatetimeIndex | None:
    """Expected trading sessions spanning a frame's index (daily only). ``None`` for
    weekly (anchored weekly bars make a session count ambiguous) or a too-short index."""
    if timeframe != "daily" or not isinstance(index, pd.DatetimeIndex) or len(index) < 2:
        return None
    return _session_calendar(index[0], index[-1])


def _slice_sessions(master: pd.DatetimeIndex | None, index: pd.Index) -> pd.DatetimeIndex | None:
    """Slice a precomputed session calendar to a single frame's date span (pure, cheap)."""
    if master is None or not isinstance(index, pd.DatetimeIndex) or len(index) < 2:
        return None
    return master[(master >= index[0]) & (master <= index[-1])]


def _split_batch(data: pd.DataFrame, tickers: list[str]) -> dict[str, pd.DataFrame]:
    """Split a ``yf.download`` result into ``{ticker: frame}``.

    With several tickers and ``group_by="ticker"`` the columns are a MultiIndex
    ``(ticker, field)``; with a single ticker they're flat. Empty/all-NaN per-ticker
    frames (a symbol that returned nothing) are dropped. Pure → unit-testable.
    """
    out: dict[str, pd.DataFrame] = {}
    if isinstance(data.columns, pd.MultiIndex):
        available = set(data.columns.get_level_values(0))
        for ticker in tickers:
            if ticker in available:
                frame = data[ticker].dropna(how="all")
                if not frame.empty:
                    out[ticker] = frame
    elif len(tickers) == 1 and not data.empty:
        frame = data.dropna(how="all")
        if not frame.empty:
            out[tickers[0]] = frame
    return out


def _extract_splits(frame: pd.DataFrame) -> pd.Series:
    """Non-zero stock splits from a ``yf.download(actions=True)`` frame (date → ratio),
    matching the shape ``data_quality`` expects. Empty Series if absent."""
    if "Stock Splits" not in frame.columns:
        return pd.Series(dtype=float)
    splits = frame["Stock Splits"]
    return splits[splits != 0]


def _last_bar_is_incomplete(last_bar_date: date, timeframe: str, now: datetime) -> bool:
    """No-lookahead guard: is the most recent bar still forming (period not yet closed)?

    Scheduled runs happen well after the close, so this is ``False`` for them — it only
    bites off-schedule (e.g. intraday) manual runs, where yfinance hands back a live
    partial bar. Holidays need no handling: a closed day has no fresh bar to drop.
    """
    now = now.astimezone(timezone.utc)
    if timeframe == "daily":
        if last_bar_date < now.date():
            return False  # bar is from a prior day -> already closed
        return now.hour < _US_CLOSE_UTC_HOUR  # today's bar, before the close -> partial
    if timeframe == "weekly":
        if last_bar_date.isocalendar()[:2] != now.date().isocalendar()[:2]:
            return False  # bar is from a prior ISO week -> closed
        weekday = now.weekday()  # Mon=0 .. Sun=6; the week closes at Friday's close
        return weekday < 4 or (weekday == 4 and now.hour < _US_CLOSE_UTC_HOUR)
    return False


def _drop_incomplete_last_bar(df: pd.DataFrame, timeframe: str, now: datetime) -> pd.DataFrame:
    """Drop the trailing bar if its period hasn't closed yet (see ``_last_bar_is_incomplete``)."""
    if df.empty:
        return df
    last = df.index[-1]
    last_date = last.date() if hasattr(last, "date") else last
    if _last_bar_is_incomplete(last_date, timeframe, now):
        return df.iloc[:-1]
    return df


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
    """Rename yfinance's capitalized OHLCV to the internal lowercase schema and
    normalize the index to tz-naive dates.

    Decouples the pipeline from the data source's naming *and* from index quirks: the
    two fetch paths return differently-typed indexes — ``Ticker.history`` is tz-aware
    (America/New_York) while ``yf.download`` is tz-naive — which otherwise wouldn't
    align (e.g. a stock vs SPY for relative strength). Daily/weekly bars carry no
    meaningful intraday time, so we strip the tz and time down to the date.
    """
    missing = [col for col in _COLUMN_MAP if col not in raw.columns]
    if missing:
        raise DataError(f"{ticker}: data missing expected column(s) {missing}.")
    out = raw.rename(columns=_COLUMN_MAP)[_OHLCV].copy()
    if isinstance(out.index, pd.DatetimeIndex):
        if out.index.tz is not None:
            out.index = out.index.tz_localize(None)
        out.index = out.index.normalize()
    return out


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
