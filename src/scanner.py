"""Orchestrator: universe × timeframe × strategies (SPEC §3, §10).

The entry point. Wires the fixed pipeline together —
``data → data_quality → features → strategy → combiner`` — for each ticker, then
writes the report / TV import file / signals.csv. Holds the fail-soft boundary: one
bad ticker is logged and skipped, never aborts the run.

M3 scope: state.py (dedup/MTF) and notify.py (Discord) are deferred to M4, so
``transition`` is "none", ``mtf_agree`` is "n/a", and no notification is sent. SPY
is not fetched yet because RS confirmation still abstains.

Run from the repo root::

    python -m src.scanner                 # all configured timeframes
    python -m src.scanner --timeframe daily
"""
from __future__ import annotations

import argparse
import dataclasses
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .combiner import combine
from .config import Config, load_config, resolve_wyckoff_params
from .data import DataError, fetch_ohlcv
from .data_quality import QualityReport, clean
from .features import compute_features
from .report import SIGNALS_COLUMNS, append_signals, render_dashboard, write_tv_import_file
from .strategies.base import StrategyContext, StrategyResult
from .strategies.registry import get_strategy
from .universe import load_universe, passes_liquidity_gate

logger = logging.getLogger(__name__)


def run_timeframe(
    timeframe: str,
    config: Config,
    today: date | None = None,
    tickers: list[str] | None = None,
    apply_liquidity_gate: bool = True,
) -> dict[str, int]:
    """Scan a universe for one timeframe and write all outputs.

    Args:
        tickers: explicit list to scan (on-demand mode); defaults to ``universe.txt``.
        apply_liquidity_gate: set False to bypass the liquidity filter (on-demand
            scans of specific names you already trust).

    Returns run counts (scanned / flagged / skipped / errored).
    """
    universe = tickers if tickers is not None else load_universe(Path(config.universe_file))
    enabled = {name: spec for name, spec in config.strategies.items() if spec.enabled}
    strategies = {name: get_strategy(name) for name in enabled}
    weights = {name: spec.weight for name, spec in enabled.items()}
    baseline_window = config.features.baseline_window[timeframe]
    params = resolve_wyckoff_params(config, timeframe)
    run_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

    counts = {"scanned": 0, "flagged": 0, "skipped": 0, "errored": 0}
    cards: list[dict[str, Any]] = []
    signal_rows: list[dict[str, Any]] = []

    for ticker in universe:
        counts["scanned"] += 1
        try:
            fetched = fetch_ohlcv(ticker, timeframe, config, today=today)

            if apply_liquidity_gate:
                passes, reason = passes_liquidity_gate(fetched.df, config.liquidity)
                if not passes:
                    counts["skipped"] += 1
                    logger.info("skip %s: %s", ticker, reason)
                    continue

            cleaned, quality = clean(fetched.df, fetched.corporate_actions, config.data_quality)
            if quality.excluded:
                counts["skipped"] += 1
                logger.info("skip %s: %s", ticker, quality.reason)
                continue

            features = compute_features(cleaned, baseline_window)
            context = StrategyContext(features=features, params=params, timeframe=timeframe)
            results = {name: strat.evaluate(cleaned, context) for name, strat in strategies.items()}
            composite = combine(results, weights)

            made_watchlist = composite.score >= config.scoring.watchlist_threshold
            signal_rows.append(
                _signals_row(run_ts, ticker, timeframe, composite, results.get("wyckoff"), features, quality, made_watchlist)
            )
            if made_watchlist and composite.direction != "none":
                counts["flagged"] += 1
                cards.append(_card(ticker, fetched.exchange, composite))

        except DataError as exc:
            counts["skipped"] += 1
            logger.info("skip %s: %s", ticker, exc)
        except Exception:  # fail soft: one bad ticker never kills the run (SPEC §10)
            counts["errored"] += 1
            logger.exception("error evaluating %s", ticker)

    render_dashboard(cards, timeframe, config, today=today, summary=counts)
    if config.output.write_tv_import_file:
        write_tv_import_file(cards, timeframe, config)
    append_signals(signal_rows, Path(config.output.dir) / "signals.csv")
    return counts


def main(argv: list[str] | None = None) -> None:
    """CLI entry: load + validate config, then run each configured timeframe."""
    parser = argparse.ArgumentParser(description="Wyckoff accumulation/distribution scanner.")
    parser.add_argument("--timeframe", choices=["daily", "weekly"], help="scan a single timeframe")
    parser.add_argument("--tickers", help="comma-separated tickers to scan instead of universe.txt (on-demand)")
    parser.add_argument("--no-liquidity-gate", action="store_true", help="bypass the liquidity filter")
    parser.add_argument("--threshold", type=float, help="override the watchlist score threshold (0-100)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    config = load_config()
    if args.threshold is not None:
        config = dataclasses.replace(config, scoring=dataclasses.replace(config.scoring, watchlist_threshold=args.threshold))

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()] if args.tickers else None
    apply_liquidity_gate = not args.no_liquidity_gate

    timeframes = [args.timeframe] if args.timeframe else config.timeframes
    for timeframe in timeframes:
        counts = run_timeframe(
            timeframe, config, tickers=tickers, apply_liquidity_gate=apply_liquidity_gate
        )
        logger.info("%s done: %s", timeframe, counts)


# --- helpers ------------------------------------------------------------------


def _card(ticker: str, exchange: str | None, composite: StrategyResult) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "exchange": exchange,
        "direction": composite.direction,
        "score": composite.score,
        "sub_scores": composite.sub_scores,
        "reasons": composite.reasons,
    }


def _signals_row(
    run_ts: str,
    ticker: str,
    timeframe: str,
    composite: StrategyResult,
    wyckoff: StrategyResult | None,
    features: pd.DataFrame,
    quality: QualityReport,
    made_watchlist: bool,
) -> dict[str, Any]:
    """Build one signals.csv row (schema ``SIGNALS_COLUMNS``) for an evaluated ticker."""
    last = features.iloc[-1] if len(features) else None
    sub = wyckoff.sub_scores if wyckoff else {}
    return {
        "run_ts": run_ts,
        "ticker": ticker,
        "timeframe": timeframe,
        "direction": composite.direction,
        "composite_score": round(composite.score, 2),
        "wyckoff_score": round(wyckoff.score, 2) if wyckoff else "",
        "range_score": _round(sub.get("range_structure")),
        "volume_score": _round(sub.get("volume_behavior")),
        "spring_score": _round(sub.get("spring_upthrust")),
        "confirmation_score": _round(sub.get("confirmation")),
        "rs_vs_spy": "",  # abstains until data.py SPY + RS scoring land
        "vol_contraction": "",  # abstains (M4)
        "mtf_agree": "n/a",  # no state.py cross-read yet (M4)
        "trend_context": _round(sub.get("confirmation")),  # confirmation = trend-only at M1
        "data_quality_flag": "; ".join(quality.repairs),
        "feat_volume_ratio": _feat(last, "volume_ratio"),
        "feat_volume_pctile": _feat(last, "volume_pctile"),
        "feat_spread_atr": _feat(last, "spread_atr"),
        "feat_spread_pctile": _feat(last, "spread_pctile"),
        "feat_close_position": _feat(last, "close_position"),
        "made_watchlist": made_watchlist,
        "transition": "none",
    }


def _round(value: float | None) -> float | str:
    return "" if value is None else round(float(value), 2)


def _feat(row: pd.Series | None, column: str) -> float | str:
    if row is None:
        return ""
    value = row.get(column)
    return "" if value is None or pd.isna(value) else round(float(value), 4)


if __name__ == "__main__":
    main()
