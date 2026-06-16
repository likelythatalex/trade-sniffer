"""Orchestrator: universe × timeframe × strategies (SPEC §3, §10).

The entry point. Wires the fixed pipeline together —
``data → data_quality → features → strategy → combiner`` — for each ticker and
timeframe, then writes the report/import file/signals log and notifies. Holds the
fail-soft boundary: one bad ticker is logged and skipped, never aborts the run.

Run from the repo root with::

    python -m src.scanner            # all configured timeframes
    python -m src.scanner --timeframe daily
"""
from __future__ import annotations

from .config import Config


def run_timeframe(timeframe: str, config: Config) -> None:
    """Scan the whole universe for one timeframe and emit all outputs.

    For each ticker: fetch → quality → features → strategy.evaluate → combine,
    then rank, write the report + TV import file, append to signals.csv, and push
    the (deduped) notification. Per-ticker errors are caught and counted here.
    """
    raise NotImplementedError


def main() -> None:
    """CLI entry: load + validate config, then run each configured timeframe."""
    raise NotImplementedError


if __name__ == "__main__":
    main()
