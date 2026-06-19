"""Orchestrator: universe × timeframe × strategies (SPEC §3, §10).

The entry point. Wires the fixed pipeline together —
``data → data_quality → features → strategy → combiner`` — for each ticker, then
writes the report / TV import file / signals.csv. Holds the fail-soft boundary: one
bad ticker is logged and skipped, never aborts the run.

State + notifications are wired (M4): prior-run state drives dedup transitions and
the MTF cross-read (the other timeframe's stored direction nudges confirmation), and
Discord fires on NEW/FAILED only (suppressed when empty). Still abstaining: RS-vs-SPY
and volatility-contraction confirmation inputs (SPY not fetched yet).

Run from the repo root::

    python -m src.scanner                 # all configured timeframes
    python -m src.scanner --timeframe daily
"""
from __future__ import annotations

import argparse
import dataclasses
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .combiner import combine
from .config import Config, load_config, resolve_wyckoff_params
from .data import fetch_many, resolve_exchange
from .data_quality import QualityReport, clean
from .features import compute_features
from .notify import has_transitions, make_notifier
from .report import (
    SIGNALS_COLUMNS,
    append_signals,
    render_dashboard,
    write_index_page,
    write_tv_import_file,
)
from .state import TimeframeState, classify_transitions, load_state, mtf_direction, save_state
from .strategies.base import StrategyContext, StrategyResult
from .strategies.registry import get_strategy
from .universe import load_universe, passes_liquidity_gate

_OTHER_TIMEFRAME = {"daily": "weekly", "weekly": "daily"}

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

    # Prior state: dedup baseline for THIS timeframe + the OTHER timeframe's MTF read.
    state_path = Path(config.output.dir) / "state.json"
    state = load_state(state_path)
    other_timeframe = _OTHER_TIMEFRAME[timeframe]
    prior_tf_state = state.get(timeframe)
    prior_qualifying = set(prior_tf_state.qualifying) if prior_tf_state else set()
    cold_start = prior_tf_state is None

    counts = {"scanned": 0, "flagged": 0, "skipped": 0, "errored": 0}
    cards: list[dict[str, Any]] = []
    signal_rows: list[dict[str, Any]] = []
    current_qualifying: dict[str, dict[str, Any]] = {}

    # Batch-fetch the whole universe up front (one threaded call, far faster at scale);
    # a ticker that returned no data is simply absent and gets skipped below.
    fetched_all = fetch_many(universe, timeframe, config, today=today)

    for ticker in universe:
        counts["scanned"] += 1
        fetched = fetched_all.get(ticker)
        if fetched is None:
            counts["skipped"] += 1
            logger.info("skip %s: no data", ticker)
            continue
        try:
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
            other_direction = mtf_direction(state, other_timeframe, ticker)  # None on cold start
            context = StrategyContext(
                features=features, params=params, timeframe=timeframe,
                prior_state=other_direction, config=config,
            )
            results = {name: strat.evaluate(cleaned, context) for name, strat in strategies.items()}
            composite = combine(results, weights)

            made_watchlist = composite.score >= config.scoring.watchlist_threshold
            signal_rows.append(
                _signals_row(run_ts, ticker, timeframe, composite, results.get("wyckoff"),
                             features, quality, made_watchlist, other_direction)
            )
            if made_watchlist and composite.direction != "none":
                counts["flagged"] += 1
                current_qualifying[ticker] = {"score": round(composite.score, 2), "direction": composite.direction}
                # Exchange is resolved here, lazily, only for the few tickers that flag.
                cards.append(_card(ticker, resolve_exchange(ticker, config), composite))

        except Exception:  # fail soft: one bad ticker never kills the run (SPEC §10)
            counts["errored"] += 1
            logger.exception("error evaluating %s", ticker)

    # Dedup transitions vs the prior run of THIS timeframe, then stamp each logged row.
    transitions = classify_transitions(prior_qualifying, set(current_qualifying))
    for row in signal_rows:
        row["transition"] = transitions.get(row["ticker"], "none")

    render_dashboard(cards, timeframe, config, today=today, summary=counts)
    write_index_page(config)  # gh-pages landing page so the bare Pages URL isn't a 404
    if config.output.write_tv_import_file:
        write_tv_import_file(cards, timeframe, config)
    append_signals(signal_rows, Path(config.output.dir) / "signals.csv")

    _notify(config, timeframe, transitions, current_qualifying, cold_start)
    state[timeframe] = TimeframeState(qualifying=current_qualifying, run_ts=run_ts)
    save_state(state_path, state)
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


def _notify(
    config: Config,
    timeframe: str,
    transitions: dict[str, str],
    current_qualifying: dict[str, dict[str, Any]],
    cold_start: bool,
) -> None:
    """Build the run summary and push it (NEW/FAILED only; suppressed when empty)."""
    notify_cfg = config.output.notify
    if not notify_cfg.enabled:
        return

    new_items = [
        {"ticker": t, "direction": current_qualifying[t]["direction"], "score": current_qualifying[t]["score"]}
        for t, transition in transitions.items()
        if transition == "new"
    ]
    failed = [t for t, transition in transitions.items() if transition == "failed"]
    summary = {
        "timeframe": timeframe,
        "new": new_items,
        "failed": failed,
        "cold_start": cold_start,
        "report_url": _report_url(notify_cfg, timeframe),
    }

    if notify_cfg.suppress_empty and not cold_start and not has_transitions(summary):
        return
    webhook = os.environ.get(notify_cfg.webhook_url_env)
    if not webhook:
        logger.info("notify skipped: %s not set", notify_cfg.webhook_url_env)
        return
    make_notifier(notify_cfg.channel, webhook).send(summary)


def _report_url(notify_cfg: Any, timeframe: str) -> str | None:
    # Link to latest_<tf>.html — always published, so the link is never stale/404.
    base = os.environ.get(notify_cfg.report_base_url_env)
    return f"{base.rstrip('/')}/latest_{timeframe}.html" if base else None


def _signals_row(
    run_ts: str,
    ticker: str,
    timeframe: str,
    composite: StrategyResult,
    wyckoff: StrategyResult | None,
    features: pd.DataFrame,
    quality: QualityReport,
    made_watchlist: bool,
    mtf_direction: str | None = None,
) -> dict[str, Any]:
    """Build one signals.csv row (schema ``SIGNALS_COLUMNS``) for an evaluated ticker.

    ``transition`` is set to "none" here and patched after classification.
    """
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
        "vol_contraction": "",  # abstains
        "mtf_agree": _mtf_agree(mtf_direction, composite.direction),
        "trend_context": _round(sub.get("confirmation")),  # confirmation = trend + MTF
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


def _mtf_agree(mtf_direction: str | None, direction: str) -> str:
    """Log-friendly MTF agreement: n/a (no other-TF read), agree, or disagree."""
    if mtf_direction is None:
        return "n/a"
    return "agree" if mtf_direction == direction else "disagree"


if __name__ == "__main__":
    main()
