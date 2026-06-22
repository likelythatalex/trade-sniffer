"""Historical replay — re-score each ticker's history with the PRODUCTION pipeline.

For each ticker we fetch the full history once, clean + compute features once, then walk
forward scoring every bar *as-of that bar*. This is correct AND fast because the pipeline
is causal — features and the strategy use trailing windows only, so computing features
once and slicing to ``:i+1`` is identical to recomputing on the prefix, with no lookahead
(``tests/test_wyckoff.py`` asserts that property). Output is one row per (ticker, as-of
bar): the signed conviction score + namespaced sub-scores, ready for outcome analysis.

Known limitations (stated honestly, surfaced in the report):
- **Survivorship bias** — replay uses today's universe, so it only ever scores companies
  that survived to today. Use for calibration/iteration; the unbiased path is analysing
  accumulated live ``signals.csv`` (same outcomes/metrics code, later).
- **MTF agreement is not replayed** — there's no historical cross-read state, so the
  ``confirmation`` sub-score here omits the MTF bonus (``prior_state=None``).
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Iterator

import pandas as pd

from ..combiner import combine
from ..config import Config, resolve_strategy_params, scoring_window
from ..data import fetch_many, fetch_spy
from ..data_quality import clean
from ..features import compute_features
from ..strategies.base import StrategyContext, StrategyResult
from ..strategies.registry import get_strategy

logger = logging.getLogger(__name__)


def replay_history(
    tickers: list[str],
    timeframe: str,
    config: Config,
    today: date | None = None,
    step: int = 1,
) -> tuple[pd.DataFrame, dict[str, pd.Series], pd.Series | None]:
    """Re-score ``tickers`` bar-by-bar for ``timeframe``. Returns
    ``(signals_df, prices, benchmark)`` for outcome analysis.

    Args:
        step: score every ``step`` bars (1 = every bar). Larger values reduce overlapping
            samples / runtime at the cost of fewer signals.

    ``signals_df`` columns: ticker, date, direction, composite_score, signed_score, and
    one ``sub_<strategy>.<name>`` column per sub-score. Tickers excluded by data quality
    are skipped (fail-soft).
    """
    today = today or date.today()
    fetched = fetch_many(tickers, timeframe, config, today=today)
    benchmark = benchmark_close(timeframe, config, today)

    prices: dict[str, pd.Series] = {}
    rows: list[dict] = []
    for ticker, i, cleaned, composite in score_history(fetched, benchmark, timeframe, config, step):
        prices[ticker] = cleaned["close"]
        rows.append(_signal_row(ticker, cleaned.index[i], composite))

    return pd.DataFrame(rows), prices, benchmark


def score_history(
    fetched: dict[str, object],
    benchmark: pd.Series | None,
    timeframe: str,
    config: Config,
    step: int = 1,
) -> Iterator[tuple[str, int, pd.DataFrame, StrategyResult]]:
    """The shared causal-replay core: yield ``(ticker, i, cleaned, composite)`` for each
    as-of bar, where ``i`` is the bar's integer position in the cleaned frame.

    Both consumers slice this differently: the IC harness keeps ``cleaned["close"]`` + the
    composite score; the plan-outcome simulator keeps ``composite.levels`` + the forward bars
    ``cleaned.iloc[i+1:]``. Causal by construction (features/strategies use trailing windows,
    so scoring on ``cleaned.iloc[:i+1]`` is lookahead-free). Quality-excluded tickers are
    skipped. MTF agreement is not replayed (``prior_state=None``)."""
    enabled = {name: spec for name, spec in config.strategies.items() if spec.enabled}
    strategies = {name: get_strategy(name) for name in enabled}
    weights = {name: spec.weight for name, spec in enabled.items()}
    baseline_window = config.features.baseline_window[timeframe]
    strategy_params = {name: resolve_strategy_params(config, name, timeframe) for name in strategies}
    start_offset = scoring_window(config, timeframe)  # first bar with full lookback windows

    for processed, (ticker, result) in enumerate(fetched.items(), start=1):
        cleaned, quality = clean(result.df, result.corporate_actions, config.data_quality, result.expected_sessions)
        if quality.excluded:
            logger.info("replay skip %s: %s", ticker, quality.reason)
            continue
        features = compute_features(cleaned, baseline_window)
        for i in range(start_offset, len(cleaned), step):
            results = {
                name: strat.evaluate(
                    cleaned.iloc[: i + 1],
                    StrategyContext(
                        features=features.iloc[: i + 1], params=strategy_params[name], timeframe=timeframe,
                        prior_state=None, benchmark_close=benchmark, config=config,  # MTF not replayed
                    ),
                )
                for name, strat in strategies.items()
            }
            yield ticker, i, cleaned, combine(results, weights)
        if processed % 50 == 0:
            logger.info("replay progress: %d/%d tickers", processed, len(fetched))


def _signal_row(ticker: str, when, composite) -> dict:
    """One as-of signal row. ``signed_score`` carries direction in its sign (+ accumulation,
    − distribution, 0 = none) so it can be ranked directly against forward returns."""
    sign = 1.0 if composite.direction == "accumulation" else -1.0 if composite.direction == "distribution" else 0.0
    row = {
        "ticker": ticker,
        "date": when,
        "direction": composite.direction,
        "composite_score": composite.score,
        "signed_score": sign * composite.score,
    }
    row.update({f"sub_{key}": value for key, value in composite.sub_scores.items()})
    return row


def benchmark_close(timeframe: str, config: Config, today: date) -> pd.Series | None:
    """SPY close for the RS input + excess-return baseline; None (abstain) on failure.

    Shared by the IC harness and the plan-outcome simulator (both score with the same
    benchmark the production pipeline uses, so the signals they evaluate match)."""
    try:
        return fetch_spy(timeframe, config, today=today)["close"]
    except Exception:
        logger.warning("benchmark (SPY) fetch failed; RS abstains and excess returns are NaN")
        return None
