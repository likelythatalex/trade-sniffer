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

import pandas as pd

from ..combiner import combine
from ..config import Config, resolve_wyckoff_params, scoring_window
from ..data import fetch_many, fetch_spy
from ..data_quality import clean
from ..features import compute_features
from ..strategies.base import StrategyContext
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
    enabled = {name: spec for name, spec in config.strategies.items() if spec.enabled}
    strategies = {name: get_strategy(name) for name in enabled}
    weights = {name: spec.weight for name, spec in enabled.items()}
    baseline_window = config.features.baseline_window[timeframe]
    params = resolve_wyckoff_params(config, timeframe)
    start_offset = scoring_window(config, timeframe)  # first bar with full lookback windows

    fetched = fetch_many(tickers, timeframe, config, today=today)
    benchmark = _benchmark_close(timeframe, config, today)

    prices: dict[str, pd.Series] = {}
    rows: list[dict] = []
    for processed, (ticker, result) in enumerate(fetched.items(), start=1):
        cleaned, quality = clean(result.df, result.corporate_actions, config.data_quality, result.expected_sessions)
        if quality.excluded:
            logger.info("replay skip %s: %s", ticker, quality.reason)
            continue
        features = compute_features(cleaned, baseline_window)
        prices[ticker] = cleaned["close"]

        for i in range(start_offset, len(cleaned), step):
            df_asof = cleaned.iloc[: i + 1]
            context = StrategyContext(
                features=features.iloc[: i + 1], params=params, timeframe=timeframe,
                prior_state=None, benchmark_close=benchmark, config=config,  # MTF not replayed
            )
            results = {name: strat.evaluate(df_asof, context) for name, strat in strategies.items()}
            composite = combine(results, weights)
            rows.append(_signal_row(ticker, cleaned.index[i], composite))

        if processed % 50 == 0:
            logger.info("replay progress: %d/%d tickers", processed, len(fetched))

    return pd.DataFrame(rows), prices, benchmark


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


def _benchmark_close(timeframe: str, config: Config, today: date) -> pd.Series | None:
    """SPY close for the RS input + excess-return baseline; None (abstain) on failure."""
    try:
        return fetch_spy(timeframe, config, today=today)["close"]
    except Exception:
        logger.warning("benchmark (SPY) fetch failed; RS abstains and excess returns are NaN")
        return None
