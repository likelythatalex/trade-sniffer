"""Tests for the news-sentiment stack (SPEC §6/§12).

Hermetic: no network. We test the pure scorer (`sentiment.py`), the news-source parsing +
day-cache (`sentiment_data.py`, with the live fetch monkeypatched), and the strategy's
as-of (no-lookahead) cutoff + abstain-vs-neutral distinction (`strategies/news_sentiment.py`).
We assert polarity *sign* and plumbing, not exact lexicon values.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from src import sentiment_data
from src.sentiment import aggregate_sentiment, get_scorer
from src.strategies.base import StrategyContext
from src.strategies.news_sentiment import NewsSentimentStrategy

POSITIVE = "Excellent results, a great win; investors are happy and optimistic"
NEGATIVE = "Terrible results, a horrible loss; investors fear the worst"


# --- pure scorer --------------------------------------------------------------


def test_aggregate_sign_and_empty() -> None:
    scorer = get_scorer("vader")
    assert aggregate_sentiment([POSITIVE], scorer) > 0
    assert aggregate_sentiment([NEGATIVE], scorer) < 0
    assert aggregate_sentiment([], scorer) is None  # no headlines -> abstain, not 0.0


def test_aggregate_is_bounded() -> None:
    scorer = get_scorer("vader")
    score = aggregate_sentiment([POSITIVE] * 5, scorer)
    assert -100.0 <= score <= 100.0


def test_get_scorer_cached_and_unknown() -> None:
    assert get_scorer("vader") is get_scorer("vader")  # lru_cache: lexicon loads once
    with pytest.raises(ValueError):
        get_scorer("does-not-exist")


# --- strategy: abstain, direction, no-lookahead -------------------------------


def _df(last="2024-06-03", periods=10) -> pd.DataFrame:
    idx = pd.date_range(end=last, periods=periods, freq="D")
    return pd.DataFrame({"close": [100.0] * periods}, index=idx)


def _ctx(headlines) -> StrategyContext:
    return StrategyContext(
        features=pd.DataFrame(), params={"lookback_days": 7, "scorer": "vader"},
        timeframe="daily", headlines=headlines,
    )


def _utc(d: str) -> datetime:
    return datetime.fromisoformat(d).replace(tzinfo=timezone.utc)


def test_abstains_when_headlines_none() -> None:
    result = NewsSentimentStrategy().evaluate(_df(), _ctx(None))
    assert result.direction == "none" and result.score == 0.0
    assert result.metadata["signed"] is None  # signed=None -> logged "" (no data, not neutral)


def test_positive_headlines_flag_accumulation() -> None:
    headlines = [(_utc("2024-06-01T12:00:00"), POSITIVE)]
    result = NewsSentimentStrategy().evaluate(_df(), _ctx(headlines))
    assert result.direction == "accumulation"
    assert result.metadata["signed"] > 0 and result.metadata["n_headlines"] == 1


def test_negative_headlines_flag_distribution() -> None:
    headlines = [(_utc("2024-06-02T09:00:00"), NEGATIVE)]
    result = NewsSentimentStrategy().evaluate(_df(), _ctx(headlines))
    assert result.direction == "distribution" and result.metadata["signed"] < 0


def test_no_lookahead_excludes_future_headlines() -> None:
    # One in-window headline + one published AFTER the evaluated bar's close (must be ignored).
    headlines = [
        (_utc("2024-06-01T12:00:00"), POSITIVE),   # in window
        (_utc("2024-06-10T12:00:00"), POSITIVE),   # future -> excluded by the as-of cutoff
    ]
    result = NewsSentimentStrategy().evaluate(_df(last="2024-06-03"), _ctx(headlines))
    assert result.metadata["n_headlines"] == 1  # only the in-window headline counted


def test_abstains_when_all_headlines_out_of_window() -> None:
    # All headlines are in the future / older than lookback -> nothing to score -> abstain.
    headlines = [(_utc("2024-06-10T12:00:00"), POSITIVE)]
    result = NewsSentimentStrategy().evaluate(_df(last="2024-06-03"), _ctx(headlines))
    assert result.direction == "none" and result.metadata["signed"] is None


# --- news source: parsing tolerance + day cache -------------------------------


def test_extract_handles_legacy_and_new_shapes() -> None:
    legacy = {"title": "Old", "providerPublishTime": 1717200000}
    assert sentiment_data._extract(legacy) == [1717200000, "Old"]
    new = {"content": {"title": "New", "pubDate": "2024-05-31T12:00:00Z"}}
    parsed = sentiment_data._extract(new)
    assert parsed is not None and parsed[1] == "New" and isinstance(parsed[0], int)
    assert sentiment_data._extract({}) is None  # neither shape -> skip, no crash


def test_iso_to_epoch_round_trip_and_garbage() -> None:
    epoch = sentiment_data._iso_to_epoch("2024-05-31T12:00:00Z")
    assert epoch == int(datetime(2024, 5, 31, 12, tzinfo=timezone.utc).timestamp())
    assert sentiment_data._iso_to_epoch("not-a-date") is None
    assert sentiment_data._iso_to_epoch(None) is None


def test_yfinance_source_day_cache(tmp_path, monkeypatch) -> None:
    calls: list[str] = []

    def fake_fetch_one(ticker: str):
        calls.append(ticker)
        return [[1717200000, f"news for {ticker}"]]

    monkeypatch.setattr(sentiment_data, "_fetch_one", fake_fetch_one)
    source = sentiment_data.YFinanceNewsSource(tmp_path)
    as_of = date(2024, 6, 3)

    first = source.fetch(["AAA"], as_of)
    assert first["AAA"][0][1] == "news for AAA"
    assert isinstance(first["AAA"][0][0], datetime)  # hydrated epoch -> datetime
    source.fetch(["AAA"], as_of)  # same day -> served from cache, no second live fetch
    assert calls == ["AAA"]  # fetched exactly once
    assert (tmp_path / "news_2024-06-03.json").exists()
