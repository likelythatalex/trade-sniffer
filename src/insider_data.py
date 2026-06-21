"""Insider-transaction (SEC Form 4) acquisition for the insider strategy (SPEC §6, §12).

The data (I/O) side, kept out of the pure strategy. A small ``InsiderSource`` interface with a
SEC EDGAR implementation so a richer source (Finnhub, etc.) drops in as one new class.

Why EDGAR: it's free, official, no API key — and **historical**, so unlike news sentiment the
insider signal is *backtestable* (Form 4s live permanently on EDGAR with filing dates). The
no-lookahead key is the **filing date** (the moment it became public), not the transaction date.

Etiquette + robustness:
- SEC requires a descriptive ``User-Agent`` (set ``EDGAR_USER_AGENT`` to add a contact) and asks
  for ≤10 req/s. We fetch sequentially and **day-cache** aggressively (one fetch/ticker/day,
  shared across the daily+weekly runs), so steady-state load is light.
- **Fail-soft per ticker** (SPEC §10): an unknown ticker, a network error, or an unparseable
  filing yields no transactions, never an aborted run. The live fetch/parse is best-effort; the
  *pure* parser (`_parse_form4`) is the tested contract.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Protocol
from xml.etree import ElementTree as ET

import requests

logger = logging.getLogger(__name__)

_CIK_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{doc}"
# A descriptive UA is required by SEC; override with a real contact via EDGAR_USER_AGENT.
_DEFAULT_USER_AGENT = "trade-sniffer/1.0 (https://github.com/likelythatalex/trade-sniffer)"
_HTTP_TIMEOUT = 20
# Bound how far back of filings we pull into the cache (covers the largest per-TF lookback,
# with margin). The strategy then filters to its own timeframe window.
_FETCH_LOOKBACK_DAYS = 220


@dataclass(frozen=True)
class InsiderTxn:
    """One non-derivative Form 4 transaction.

    ``filing_date`` is the public-availability date used for the no-lookahead cutoff.
    ``code`` is the SEC transaction code (``P`` = open-market purchase, ``S`` = sale, and
    others like ``A``/``M``/``G`` the strategy treats as noise). ``value`` is shares × price
    (0 when price is absent). ``acquired`` is the A/D flag. ``owner`` enables cluster counting.
    """

    filing_date: date
    code: str
    shares: float
    value: float
    acquired: bool
    owner: str


class InsiderSource(Protocol):
    """Fetches recent insider transactions per ticker. Implementations must be fail-soft."""

    def fetch(self, tickers: list[str], as_of: date) -> dict[str, list[InsiderTxn]]:
        ...


def build_insider_source(name: str, cache_dir: Path) -> InsiderSource:
    """Return the insider source registered under ``name`` (config ``insider.source``).

    Raises:
        ValueError: on an unknown source — fail loud, don't silently disable the strategy.
    """
    if name == "edgar":
        return EdgarInsiderSource(cache_dir)
    raise ValueError(f"Unknown insider source '{name}'. Known: edgar.")


class EdgarInsiderSource:
    """Form 4 transactions via SEC EDGAR, day-cached under ``cache_dir``."""

    def __init__(self, cache_dir: Path, user_agent: str | None = None) -> None:
        import os

        self._cache_dir = Path(cache_dir)
        self._headers = {"User-Agent": user_agent or os.environ.get("EDGAR_USER_AGENT", _DEFAULT_USER_AGENT)}
        self._cik_map: dict[str, str] | None = None

    def fetch(self, tickers: list[str], as_of: date) -> dict[str, list[InsiderTxn]]:
        cache_path = self._cache_dir / f"insider_{as_of.isoformat()}.json"
        cache = _load_cache(cache_path)
        missing = [t for t in tickers if t not in cache]
        if missing:
            for ticker in missing:
                cache[ticker] = self._fetch_one(ticker, as_of)
            _save_cache(cache_path, cache)
        return {ticker: [_row_to_txn(row) for row in cache.get(ticker, [])] for ticker in tickers}

    # --- one ticker (best-effort; any failure -> []) ---------------------------------------

    def _fetch_one(self, ticker: str, as_of: date) -> list[list[Any]]:
        try:
            cik = self._cik_for(ticker)
            if cik is None:
                return []
            subs = self._get_json(_SUBMISSIONS_URL.format(cik=cik))
            recent = subs["filings"]["recent"]
            earliest = as_of - timedelta(days=_FETCH_LOOKBACK_DAYS)
            rows: list[list[Any]] = []
            for form, filed, accession, doc in zip(
                recent["form"], recent["filingDate"], recent["accessionNumber"], recent["primaryDocument"]
            ):
                if form != "4":
                    continue
                filing_date = date.fromisoformat(filed)
                if not (earliest <= filing_date <= as_of):
                    continue
                xml = self._get_text(
                    _ARCHIVE_URL.format(cik=int(cik), accession=accession.replace("-", ""), doc=doc)
                )
                rows.extend(_txn_to_row(t) for t in _parse_form4(xml, filing_date))
            return rows
        except Exception:  # unknown ticker, network, shape change — never kill the run
            logger.debug("insider fetch failed for %s", ticker, exc_info=True)
            return []

    def _cik_for(self, ticker: str) -> str | None:
        if self._cik_map is None:
            self._cik_map = _load_cik_map(self._cache_dir, self._get_json)
        return self._cik_map.get(ticker.upper())

    def _get_json(self, url: str) -> Any:
        resp = requests.get(url, headers=self._headers, timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def _get_text(self, url: str) -> str:
        resp = requests.get(url, headers=self._headers, timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
        return resp.text


# --- pure parsing (the tested contract; live fetch shape is best-effort) ------------------


def _parse_form4(xml: str, filing_date: date) -> list[InsiderTxn]:
    """Extract non-derivative transactions from a Form 4 ownership XML document.

    Returns ``[]`` on unparseable input. The SEC ownership XML is namespace-free; we read the
    transaction code, share count, price, and acquired/disposed flag per transaction, plus the
    document-level reporting owner (for cluster counting)."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    owner = (root.findtext(".//reportingOwner/reportingOwnerId/rptOwnerName") or "").strip()
    txns: list[InsiderTxn] = []
    for node in root.findall(".//nonDerivativeTransaction"):
        shares = _to_float(node.findtext("./transactionAmounts/transactionShares/value"))
        if shares is None:
            continue
        code = (node.findtext("./transactionCoding/transactionCode") or "").strip()
        price = _to_float(node.findtext("./transactionAmounts/transactionPricePerShare/value")) or 0.0
        acquired = (node.findtext("./transactionAmounts/transactionAcquiredDisposedCode/value") or "").strip() == "A"
        txns.append(InsiderTxn(filing_date, code, shares, shares * price, acquired, owner))
    return txns


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# --- CIK map + day cache (best-effort JSON; a miss is just an empty result) ---------------


def _load_cik_map(cache_dir: Path, getter: Any) -> dict[str, str]:
    """Ticker → 10-digit zero-padded CIK. Cached to disk (the map changes rarely)."""
    path = Path(cache_dir) / "edgar_cik_map.json"
    cached = _load_cache(path)
    if cached:
        return cached
    try:
        raw = getter(_CIK_MAP_URL)
        mapping = {
            str(row["ticker"]).upper(): str(row["cik_str"]).zfill(10)
            for row in raw.values()
        }
    except Exception:
        logger.debug("EDGAR CIK map fetch failed", exc_info=True)
        return {}
    _save_cache(path, mapping)
    return mapping


def _txn_to_row(txn: InsiderTxn) -> list[Any]:
    return [txn.filing_date.isoformat(), txn.code, txn.shares, txn.value, txn.acquired, txn.owner]


def _row_to_txn(row: list[Any]) -> InsiderTxn:
    filed, code, shares, value, acquired, owner = row
    return InsiderTxn(date.fromisoformat(filed), code, float(shares), float(value), bool(acquired), owner)


def _load_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.debug("insider cache unreadable at %s; treating as empty", path, exc_info=True)
        return {}


def _save_cache(path: Path, data: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        logger.debug("could not write insider cache at %s", path, exc_info=True)
