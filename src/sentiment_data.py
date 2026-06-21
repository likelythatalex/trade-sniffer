"""News headline acquisition for the news-sentiment strategy (SPEC §6, §12).

The data (I/O) side, kept out of the pure strategy. A small ``NewsSource`` interface with a
yfinance implementation so a richer source (Finnhub, StockTwits, …) drops in as one new
class — the "design for swap" the strategy depends on.

Two properties matter:
- **Fail-soft per ticker.** No news / parse failure / network error → empty list, never an
  aborted run (SPEC §10). A skipped headline beats a dead scan.
- **Day-cached.** News is timeframe-independent, so one fetch per ticker per calendar day is
  shared across the daily and weekly runs (the second is a cheap cache hit) and across
  same-day re-runs — this is what keeps whole-universe news fetching affordable.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

#: One headline: (published UTC datetime, title).
NewsItem = tuple[datetime, str]


class NewsSource(Protocol):
    """Fetches recent headlines per ticker. Implementations must be fail-soft."""

    def fetch(self, tickers: list[str], as_of: date) -> dict[str, list[NewsItem]]:
        ...


class YFinanceNewsSource:
    """Recent headlines via ``yfinance``, cached once per calendar day under ``cache_dir``."""

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = Path(cache_dir)

    def fetch(self, tickers: list[str], as_of: date) -> dict[str, list[NewsItem]]:
        cache_path = self._cache_dir / f"news_{as_of.isoformat()}.json"
        cache = _load_cache(cache_path)
        missing = [t for t in tickers if t not in cache]
        if missing:
            for ticker in missing:
                cache[ticker] = _fetch_one(ticker)
            _save_cache(cache_path, cache)
        # Cache stores JSON-friendly [epoch, title]; hydrate to (datetime, title) for callers.
        return {
            ticker: [(_epoch_to_utc(epoch), title) for epoch, title in cache.get(ticker, [])]
            for ticker in tickers
        }


def build_news_source(name: str, cache_dir: Path) -> NewsSource:
    """Return the news source registered under ``name`` (config ``sentiment.source``).

    Raises:
        ValueError: on an unknown source — fail loud, don't silently disable sentiment.
    """
    if name == "yfinance":
        return YFinanceNewsSource(cache_dir)
    raise ValueError(f"Unknown news source '{name}'. Known: yfinance.")


# --- fetch + parse (defensive: yfinance's news shape has changed across versions) ---------


def _fetch_one(ticker: str) -> list[list[Any]]:
    """Recent headlines for one ticker as JSON-friendly ``[epoch, title]`` rows. Fail-soft."""
    try:
        import yfinance as yf

        raw = yf.Ticker(ticker).news or []
    except Exception:  # network, rate-limit, attribute changes — never kill the run
        logger.debug("news fetch failed for %s", ticker, exc_info=True)
        return []
    rows: list[list[Any]] = []
    for item in raw:
        parsed = _extract(item)
        if parsed is not None:
            rows.append(parsed)
    return rows


def _extract(item: Any) -> list[Any] | None:
    """Pull ``[epoch, title]`` from one yfinance news item, tolerating both the legacy flat
    shape (``title`` + ``providerPublishTime``) and the newer nested ``content`` shape
    (``content.title`` + ``content.pubDate``). Returns ``None`` if neither yields both fields."""
    if not isinstance(item, dict):
        return None
    content = item.get("content")
    if isinstance(content, dict):  # newer yfinance
        title = content.get("title")
        epoch = _iso_to_epoch(content.get("pubDate") or content.get("displayTime"))
        if title and epoch is not None:
            return [epoch, str(title)]
    title = item.get("title")  # legacy yfinance
    epoch = item.get("providerPublishTime")
    if title and isinstance(epoch, (int, float)):
        return [int(epoch), str(title)]
    return None


def _iso_to_epoch(value: Any) -> int | None:
    """ISO-8601 string (e.g. ``2024-05-31T12:00:00Z``) → UTC epoch seconds, or ``None``."""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _epoch_to_utc(epoch: Any) -> datetime:
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc)


# --- day cache (best-effort JSON; a corrupt/missing file is just a miss) ------------------


def _load_cache(path: Path) -> dict[str, list[list[Any]]]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.debug("news cache unreadable at %s; treating as empty", path, exc_info=True)
        return {}


def _save_cache(path: Path, cache: dict[str, list[list[Any]]]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache), encoding="utf-8")
    except OSError:
        logger.debug("could not write news cache at %s", path, exc_info=True)
