"""Tests for the insider-transaction stack (SPEC §6/§12).

Hermetic: no network. We test the Form 4 XML parser (`insider_data._parse_form4`, the tested
contract — the live EDGAR fetch is best-effort around it), the day-cache, the pure relative
scoring (net buy/sell ratio + sell_weight), and the strategy's filing-date no-lookahead cutoff
and abstain-vs-neutral distinction. We assert sign + plumbing, not magic numbers.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from src.insider_data import EdgarInsiderSource, InsiderTxn, _parse_form4
from src.strategies.base import StrategyContext
from src.strategies.insider import InsiderStrategy

# A minimal but realistic Form 4 ownership document: one purchase (P/A) + one sale (S/D).
FORM4_XML = """<?xml version="1.0"?>
<ownershipDocument>
  <reportingOwner><reportingOwnerId><rptOwnerName>DOE JANE</rptOwnerName></reportingOwnerId></reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>1000</value></transactionShares>
        <transactionPricePerShare><value>50</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>400</value></transactionShares>
        <transactionPricePerShare><value>50</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>"""


# --- Form 4 parsing -----------------------------------------------------------


def test_parse_form4_extracts_transactions() -> None:
    txns = _parse_form4(FORM4_XML, date(2024, 5, 1))
    assert len(txns) == 2
    buy = next(t for t in txns if t.code == "P")
    assert buy.shares == 1000 and buy.value == 50000 and buy.acquired and buy.owner == "DOE JANE"
    assert buy.filing_date == date(2024, 5, 1)
    sell = next(t for t in txns if t.code == "S")
    assert sell.value == 20000 and not sell.acquired


def test_parse_form4_garbage_is_empty() -> None:
    assert _parse_form4("not xml at all", date(2024, 5, 1)) == []
    assert _parse_form4("<ownershipDocument></ownershipDocument>", date(2024, 5, 1)) == []


def test_edgar_source_day_cache(tmp_path, monkeypatch) -> None:
    calls: list[str] = []

    def fake_fetch_one(self, ticker, as_of):
        calls.append(ticker)
        return [[as_of.isoformat(), "P", 1000.0, 50000.0, True, "DOE JANE"]]

    monkeypatch.setattr(EdgarInsiderSource, "_fetch_one", fake_fetch_one)
    source = EdgarInsiderSource(tmp_path)
    as_of = date(2024, 6, 3)

    first = source.fetch(["AAA"], as_of)
    assert isinstance(first["AAA"][0], InsiderTxn) and first["AAA"][0].code == "P"
    source.fetch(["AAA"], as_of)  # same day -> cache hit, no second live fetch
    assert calls == ["AAA"]
    assert (tmp_path / "insider_2024-06-03.json").exists()


# --- pure scoring -------------------------------------------------------------


def _txn(code: str, value: float, owner: str = "X", filed=date(2024, 6, 1)) -> InsiderTxn:
    return InsiderTxn(filed, code, value / 50.0, value, code == "P", owner)


def _score(txns, sell_weight=0.5):
    from src.strategies.insider import _score as score_fn

    return score_fn(txns, sell_weight)


def test_score_buys_positive_sells_negative() -> None:
    pos, _ = _score([_txn("P", 50000)])
    neg, _ = _score([_txn("S", 50000)])
    assert pos == 100.0 and neg == -100.0


def test_score_sell_weight_tilts_net_buyish() -> None:
    # Equal buy/sell value, but sells count half -> net positive lean.
    signed, breakdown = _score([_txn("P", 50000), _txn("S", 50000)], sell_weight=0.5)
    assert signed > 0  # (50000 - 25000)/(50000 + 25000) = +33.3
    assert breakdown["n_buys"] == 1 and breakdown["n_sells"] == 1


def test_score_counts_distinct_buy_owners_for_cluster() -> None:
    _, breakdown = _score([_txn("P", 10000, owner="A"), _txn("P", 10000, owner="B")])
    assert breakdown["n_buy_owners"] == 2


def test_score_ignores_non_open_market_codes_and_abstains() -> None:
    # Awards (A) / option exercises (M) are noise -> no P/S -> abstain.
    signed, _ = _score([_txn("A", 99999), _txn("M", 99999)])
    assert signed is None


# --- strategy: abstain + no-lookahead -----------------------------------------


def _df(last="2024-06-03", periods=5) -> pd.DataFrame:
    idx = pd.date_range(end=last, periods=periods, freq="D")
    return pd.DataFrame({"close": [100.0] * periods}, index=idx)


def _ctx(txns) -> StrategyContext:
    return StrategyContext(
        features=pd.DataFrame(), params={"lookback_days": 90, "sell_weight": 0.5},
        timeframe="daily", insider_transactions=txns,
    )


def test_strategy_abstains_without_data() -> None:
    result = InsiderStrategy().evaluate(_df(), _ctx(None))
    assert result.direction == "none" and result.metadata["signed"] is None


def test_strategy_flags_accumulation_on_net_buying() -> None:
    result = InsiderStrategy().evaluate(_df(), _ctx([_txn("P", 50000, filed=date(2024, 5, 20))]))
    assert result.direction == "accumulation" and result.metadata["signed"] > 0


def test_strategy_no_lookahead_excludes_future_filings() -> None:
    txns = [
        _txn("P", 50000, filed=date(2024, 5, 20)),   # in window
        _txn("S", 90000, filed=date(2024, 6, 10)),   # filed AFTER the bar -> must be excluded
    ]
    result = InsiderStrategy().evaluate(_df(last="2024-06-03"), _ctx(txns))
    assert result.metadata["n_buys"] == 1 and result.metadata["n_sells"] == 0
    assert result.direction == "accumulation"  # the future sale didn't drag it negative
