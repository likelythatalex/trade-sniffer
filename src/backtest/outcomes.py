"""Forward-return outcomes for backtesting (Tier 3). Pure — prices are passed in.

Given as-of signal rows + price series, attach each signal's forward return over a set
of horizons and its **excess return vs the benchmark (SPY)** over the same window.
Excess return strips out market drift, so a rising tide doesn't flatter every
"accumulation" flag — it's the difference between an honest result and a misleading one.
"""
from __future__ import annotations

import pandas as pd


def add_forward_returns(
    signals: pd.DataFrame,
    prices: dict[str, pd.Series],
    benchmark: pd.Series | None,
    horizons: list[int],
) -> pd.DataFrame:
    """Return a copy of ``signals`` with ``fwd_return_{h}`` and ``excess_return_{h}``
    columns for each horizon.

    Args:
        signals: rows with at least ``ticker`` and ``date`` (the as-of bar).
        prices: ticker -> close Series, on the index replay scored on.
        benchmark: benchmark (SPY) close Series, or ``None`` -> excess returns are NaN.
        horizons: forward bar offsets (e.g. ``[5, 10, 20]``).

    A signal too close to its series end (no bar at ``date + h``) gets NaN for that
    horizon (metrics drop NaN pairs). Returns are simple close-to-close.
    """
    rows = signals.copy()
    # Vectorize per series: forward return at each date = close[t+h]/close[t] - 1.
    bench_fwd = {h: _series_forward_returns(benchmark, h) for h in horizons}
    ticker_fwd = {
        ticker: {h: _series_forward_returns(close, h) for h in horizons}
        for ticker, close in prices.items()
    }

    for h in horizons:
        bench_h = bench_fwd[h]
        fwd_col: list[float | None] = []
        excess_col: list[float | None] = []
        for ticker, date in zip(rows["ticker"], rows["date"]):
            stock_ret = _lookup(ticker_fwd.get(ticker, {}).get(h), date)
            bench_ret = _lookup(bench_h, date)
            fwd_col.append(stock_ret)
            excess_col.append(
                stock_ret - bench_ret if (stock_ret is not None and bench_ret is not None) else None
            )
        rows[f"fwd_return_{h}"] = fwd_col
        rows[f"excess_return_{h}"] = excess_col
    return rows


def _series_forward_returns(close: pd.Series | None, horizon: int) -> pd.Series | None:
    """Close-to-close return ``horizon`` bars ahead, aligned to each starting date."""
    if close is None or len(close) == 0:
        return None
    return close.shift(-horizon) / close - 1.0


def _lookup(series: pd.Series | None, date) -> float | None:
    if series is None or date not in series.index:
        return None
    value = series.loc[date]
    return None if pd.isna(value) else float(value)
