"""CLI for the offline backtester (Tier 3). Never on the scheduled path.

Examples (from the repo root)::

    python -m src.backtest --timeframe daily --tickers AAPL,XOM,KO
    python -m src.backtest --timeframe daily --limit 50 --horizons 5,10,20
    python -m src.backtest --timeframe weekly --step 1

Writes a markdown summary + raw rows CSV to ``backtest_results/``.
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from ..config import load_config
from ..universe import load_universe
from .metrics import compute_report
from .outcomes import add_forward_returns
from .replay import replay_history
from .report import render_report

_RESULTS_DIR = Path("backtest_results")

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Offline backtester for the conviction score.")
    parser.add_argument("--timeframe", choices=["daily", "weekly"], default="daily")
    parser.add_argument("--tickers", help="comma-separated tickers (default: universe.txt)")
    parser.add_argument("--limit", type=int, help="cap the universe to the first N tickers (speed)")
    parser.add_argument("--horizons", default="5,10,20", help="forward bar offsets, comma-separated")
    parser.add_argument("--step", type=int, default=1, help="score every Nth bar (default 1)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    config = load_config()
    horizons = [int(h) for h in args.horizons.split(",") if h.strip()]
    tickers = _resolve_tickers(args, config)
    logger.info("replaying %d tickers on %s (horizons=%s, step=%d)", len(tickers), args.timeframe, horizons, args.step)

    signals, prices, benchmark = replay_history(tickers, args.timeframe, config, step=args.step)
    if signals.empty:
        logger.warning("no signals produced (no data / all excluded); nothing to report.")
        return

    enriched = add_forward_returns(signals, prices, benchmark, horizons)
    report = compute_report(enriched, horizons)
    path = render_report(report, enriched, _RESULTS_DIR, args.timeframe)
    logger.info("backtest report written: %s", path)


def _resolve_tickers(args: argparse.Namespace, config) -> list[str]:
    if args.tickers:
        return [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    universe = load_universe(Path(config.universe_file))
    return universe[: args.limit] if args.limit else universe


if __name__ == "__main__":
    main()
