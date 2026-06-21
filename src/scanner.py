"""Orchestrator: universe × timeframe × strategies (SPEC §3, §10).

The entry point. Wires the fixed pipeline together —
``data → data_quality → features → strategy → combiner`` — for each ticker, then
writes the report / TV import file / signals.csv. Holds the fail-soft boundary: one
bad ticker is logged and skipped, never aborts the run.

State + notifications are wired (M4): prior-run state drives dedup transitions and
the MTF cross-read (the other timeframe's stored direction nudges confirmation), and
Discord fires on NEW/FAILED only (suppressed when empty). RS-vs-SPY is wired (SPY is
batch-fetched once per timeframe and passed into the strategy); volatility contraction
is the remaining abstaining confirmation input.

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
from .config import Config, load_config, resolve_market_params, resolve_strategy_params
from .data import fetch_many, fetch_spy, resolve_exchange
from .data_quality import QualityReport, clean
from .features import compute_features
from .notify import has_transitions, make_notifier
from .review import review_candidates
from .market_context import compute_market_context
from .report import (
    SIGNALS_COLUMNS,
    append_market,
    append_signals,
    render_dashboard,
    write_index_page,
    write_tv_import_file,
)
from .insider_data import build_insider_source
from .sentiment_data import build_news_source
from .state import TimeframeState, classify_transitions, load_state, mtf_direction, save_state
from .strategies.base import Levels, StrategyContext, StrategyResult
from .strategies.registry import get_strategy
from .trade_plan import plan_trade
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
    # Each strategy gets its OWN resolved params (not Wyckoff's) — the multi-strategy plumbing.
    strategy_params = {name: resolve_strategy_params(config, name, timeframe) for name in strategies}
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
    skipped_detail: list[tuple[str, str]] = []  # (ticker, reason) — surfaced in the run summary
    errored_detail: list[str] = []

    # Batch-fetch the whole universe up front (one threaded call, far faster at scale);
    # a ticker that returned no data is simply absent and gets skipped below.
    fetched_all = fetch_many(universe, timeframe, config, today=today)
    benchmark = _benchmark_close(timeframe, config, today)  # SPY for RS; None -> RS abstains
    # Whole-universe headlines for news-sentiment (only when that strategy is enabled).
    headlines_all = _fetch_headlines(universe, config, today) if "news_sentiment" in strategies else {}
    # Whole-universe insider (Form 4) transactions, same gating + rationale.
    insider_all = _fetch_insider(universe, config, today) if "insider" in strategies else {}

    for ticker in universe:
        counts["scanned"] += 1
        fetched = fetched_all.get(ticker)
        if fetched is None:
            counts["skipped"] += 1
            skipped_detail.append((ticker, "no data"))
            continue
        try:
            if apply_liquidity_gate:
                passes, reason = passes_liquidity_gate(fetched.df, config.liquidity)
                if not passes:
                    counts["skipped"] += 1
                    skipped_detail.append((ticker, reason))
                    continue

            cleaned, quality = clean(
                fetched.df, fetched.corporate_actions, config.data_quality, fetched.expected_sessions
            )
            if quality.excluded:
                counts["skipped"] += 1
                skipped_detail.append((ticker, quality.reason or "data quality"))
                continue

            features = compute_features(cleaned, baseline_window)
            other_direction = mtf_direction(state, other_timeframe, ticker)  # None on cold start
            # Per-strategy context: same features/state, but each strategy's own params.
            results = {
                name: strat.evaluate(
                    cleaned,
                    StrategyContext(
                        features=features, params=strategy_params[name], timeframe=timeframe,
                        prior_state=other_direction, benchmark_close=benchmark,
                        headlines=headlines_all.get(ticker),
                        insider_transactions=insider_all.get(ticker), config=config,
                    ),
                )
                for name, strat in strategies.items()
            }
            composite = combine(results, weights)

            made_watchlist = composite.score >= config.scoring.watchlist_threshold
            signal_rows.append(
                _signals_row(run_ts, ticker, timeframe, composite, results.get("wyckoff"),
                             features, cleaned, quality, made_watchlist, other_direction,
                             momentum=results.get("momentum"),
                             news_sentiment=results.get("news_sentiment"),
                             insider=results.get("insider"))
            )
            if made_watchlist and composite.direction != "none":
                counts["flagged"] += 1
                current_qualifying[ticker] = {"score": round(composite.score, 2), "direction": composite.direction}
                # Exchange is resolved here, lazily, only for the few tickers that flag.
                cards.append(_card(ticker, resolve_exchange(ticker, config), composite, results.get("wyckoff"), cleaned, config))

        except Exception:  # fail soft: one bad ticker never kills the run (SPEC §10)
            counts["errored"] += 1
            errored_detail.append(ticker)
            logger.exception("error evaluating %s", ticker)

    # Surface WHY names dropped (consolidated, not one line per ticker buried in the log).
    if skipped_detail:
        logger.info("skipped %d: %s", len(skipped_detail), "; ".join(f"{t} ({r})" for t, r in skipped_detail))
    if errored_detail:
        logger.info("errored %d: %s", len(errored_detail), ", ".join(errored_detail))

    # Dedup transitions vs the prior run of THIS timeframe, then stamp each logged row.
    transitions = classify_transitions(prior_qualifying, set(current_qualifying))
    for row in signal_rows:
        row["transition"] = transitions.get(row["ticker"], "none")

    # Objective due-diligence review of NEWLY-flagged setups (bounded/cached/off by default),
    # attached to the cards before rendering so it shows on the dashboard.
    review_candidates(
        cards, transitions, timeframe, config,
        today or date.today(), Path(config.output.dir) / "reviews.json",
    )

    # Market context: one market-wide reading (regime + breadth) from the data already fetched.
    # Displayed on the dashboard + logged to market.csv; not (yet) applied to per-ticker scores.
    market_ma = int(resolve_market_params(config, timeframe)["ma_window"])
    universe_closes = [fr.df["close"] for fr in fetched_all.values() if fr is not None and "close" in fr.df]
    market = dataclasses.asdict(compute_market_context(benchmark, universe_closes, market_ma))
    append_market({"run_ts": run_ts, "timeframe": timeframe, **market}, Path(config.output.dir) / "market.csv")

    summary = {**counts, "skipped_detail": skipped_detail, "errored_detail": errored_detail}
    render_dashboard(cards, timeframe, config, today=today, summary=summary, market=market)
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


def _benchmark_close(timeframe: str, config: Config, today: date | None) -> pd.Series | None:
    """SPY close series for the RS-vs-SPY confirmation input (§7.1). Fetched once per
    timeframe. A missing benchmark must never abort the run, so failures → ``None``
    (RS simply abstains for the whole run)."""
    try:
        return fetch_spy(timeframe, config, today=today)["close"]
    except Exception:
        logger.warning("benchmark (SPY) fetch failed; RS-vs-SPY abstains this run")
        return None


def _fetch_headlines(
    universe: list[str], config: Config, today: date | None
) -> dict[str, list[tuple[Any, str]]]:
    """Recent headlines per ticker for the news-sentiment strategy, fetched once per run
    (day-cached, so the other timeframe's run reuses it). Whole-universe by design — the
    forward calibration study needs every evaluated name, not just flagged ones (else
    selection bias). A failure anywhere → ``{}`` so the strategy simply abstains; news must
    never abort a scan (SPEC §10)."""
    try:
        source = build_news_source(config.sentiment.defaults["source"], Path(config.data.cache_dir))
        return source.fetch(universe, today or date.today())
    except Exception:
        logger.warning("news fetch failed; news-sentiment abstains this run")
        return {}


def _fetch_insider(
    universe: list[str], config: Config, today: date | None
) -> dict[str, list[Any]]:
    """Whole-universe insider (Form 4) transactions for the insider strategy, fetched once per
    run (day-cached). Same rationale as news: every evaluated name (no selection bias) and
    fail-soft (`{}` → strategy abstains) so it never aborts a scan (SPEC §10)."""
    try:
        source = build_insider_source(config.insider.defaults["source"], Path(config.data.cache_dir))
        return source.fetch(universe, today or date.today())
    except Exception:
        logger.warning("insider fetch failed; insider strategy abstains this run")
        return {}


def _card(
    ticker: str,
    exchange: str | None,
    composite: StrategyResult,
    wyckoff: StrategyResult | None,
    df: pd.DataFrame,
    config: Config,
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "exchange": exchange,
        "direction": composite.direction,
        "score": composite.score,
        "sub_scores": composite.sub_scores,
        "reasons": composite.reasons,
        "chart": _chart_data(df, wyckoff),
        "plan": _plan_data(composite.direction, composite.levels, config),
    }


def _plan_data(direction: str, levels: Levels, config: Config) -> dict[str, Any] | None:
    """Serialize the suggested trade plan for the card (rounded for display), or ``None``
    when the planner abstains. Display-only — these are suggested levels, never executed."""
    plan = plan_trade(direction, levels, config.trade_plan)
    if plan is None:
        return None
    return {
        "entry": round(plan.entry, 2),
        "stop": round(plan.stop, 2),
        "target": round(plan.target, 2),
        "reward_risk": round(plan.reward_risk, 2),
        "risk_per_share": round(plan.risk_per_share, 2),
        "size_shares": round(plan.size_shares),
        "position_value": round(plan.position_value),
        "risk_amount": round(plan.risk_amount, 2),
        "management": plan.management,
    }


_CHART_MAX_BARS = 250  # bars of history embedded per chart (keeps the page lean)


def _chart_data(df: pd.DataFrame, wyckoff: StrategyResult | None, max_bars: int = _CHART_MAX_BARS) -> dict[str, Any]:
    """OHLCV + annotations for one ticker's Lightweight Chart, embedded in the page.

    Annotations (range band, spring/upthrust + climax markers) come from the Wyckoff
    result's metadata. This is the one strategy-specific bit of the chart; future
    strategies that want their own overlays would surface them the same way.
    """
    window = df.iloc[-max_bars:]
    candles = [
        {
            "time": _chart_time(ts),
            "open": round(float(row.open), 2),
            "high": round(float(row.high), 2),
            "low": round(float(row.low), 2),
            "close": round(float(row.close), 2),
        }
        for ts, row in window.iterrows()
    ]
    volume = [
        {"time": _chart_time(ts), "value": round(float(row.volume), 0),
         "up": bool(row.close >= row.open)}
        for ts, row in window.iterrows()
    ]

    meta = wyckoff.metadata if wyckoff else {}
    range_info = meta.get("range", {})

    # Chart markers: the Phase-C shake (Spring / UTAD) and the confirmed climax (SC / BC).
    # The template maps each marker `type` to its shape/position/label.
    markers: list[dict[str, Any]] = []
    spring_bar = meta.get("spring_bar")
    if spring_bar is not None:
        markers.append({"time": _chart_time(spring_bar), "type": "spring" if meta.get("is_spring") else "upthrust"})
    climax_bar = meta.get("climax_bar")
    if climax_bar is not None:
        markers.append({"time": _chart_time(climax_bar), "type": meta.get("climax_type")})

    return {
        "candles": candles,
        "volume": volume,
        "range_high": _maybe_round(range_info.get("range_high")),
        "range_low": _maybe_round(range_info.get("range_low")),
        "markers": markers,
    }


def _chart_time(ts: Any) -> Any:
    """Lightweight Charts time: an ISO date for real bars; the raw value otherwise (test
    fixtures use a plain integer index — keeps chart-data building hermetic)."""
    return ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else int(ts)


def _maybe_round(value: Any) -> float | None:
    return None if value is None else round(float(value), 2)


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
    prices: pd.DataFrame,
    quality: QualityReport,
    made_watchlist: bool,
    mtf_direction: str | None = None,
    momentum: StrategyResult | None = None,
    news_sentiment: StrategyResult | None = None,
    insider: StrategyResult | None = None,
) -> dict[str, Any]:
    """Build one signals.csv row (schema ``SIGNALS_COLUMNS``) for an evaluated ticker.

    ``prices`` is the cleaned OHLCV (aligned to ``features``); its last bar's raw
    close/volume are logged so forward outcomes can be derived from the log alone.
    ``transition`` is set to "none" here and patched after classification.
    """
    last = features.iloc[-1] if len(features) else None
    last_bar = prices.iloc[-1] if len(prices) else None
    sub = wyckoff.sub_scores if wyckoff else {}
    conf = wyckoff.metadata.get("confirmation", {}) if wyckoff else {}
    return {
        "run_ts": run_ts,
        "ticker": ticker,
        "timeframe": timeframe,
        "direction": composite.direction,
        "composite_score": round(composite.score, 2),
        "wyckoff_score": round(wyckoff.score, 2) if wyckoff else "",
        # Signed momentum composite (independent signal; logged for the correlation study).
        "momentum_score": _round(momentum.metadata.get("signed")) if momentum else "",
        # Signed news-sentiment composite; "" = no data (abstained), 0.0 = neutral. Forward-only.
        "news_sentiment_score": _round(news_sentiment.metadata.get("signed")) if news_sentiment else "",
        # Signed insider composite (Form 4 net buy/sell ratio); "" = no data, 0.0 = balanced.
        "insider_score": _round(insider.metadata.get("signed")) if insider else "",
        "range_score": _round(sub.get("range_structure")),
        "volume_score": _round(sub.get("volume_behavior")),
        "spring_score": _round(sub.get("spring_upthrust")),
        "confirmation_score": _round(sub.get("confirmation")),
        "rs_vs_spy": _round(conf.get("rs")),  # signed RS contribution, or "" if it abstained
        "vol_contraction": _round(conf.get("vol_contraction")),
        "mtf_agree": _mtf_agree(mtf_direction, composite.direction),
        "trend_context": _round(conf.get("trend")),
        "data_quality_flag": "; ".join(quality.repairs),
        "close": _feat(last_bar, "close"),
        "volume": _feat(last_bar, "volume"),
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
