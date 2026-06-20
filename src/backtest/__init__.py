"""Offline backtesting for trade-sniffer (Tier 3 — ROADMAP).

**Not part of the scheduled scan.** This subpackage is an on-demand analysis tool: it
re-scores history with the *production* pipeline (so we validate the code we actually
ship) and measures whether the conviction score is *informative* — it does not grade the
qualitative Wyckoff read, it grades the number.

Run it with ``python -m src.backtest`` (see ``__main__``). Results go to
``backtest_results/`` (gitignored; never published to gh-pages).

Pieces:
- ``replay``   — re-score each ticker's history bar-by-bar, as-of each bar (causal).
- ``outcomes`` — forward returns + excess-vs-SPY per horizon.
- ``metrics``  — Information Coefficient, by-bucket returns, hit-rate lift, per-sub-score IC.
- ``report``   — markdown + CSV summary (with the survivorship caveat front and centre).
"""
from __future__ import annotations

from .metrics import compute_report, information_coefficient
from .outcomes import add_forward_returns
from .replay import replay_history

__all__ = ["replay_history", "add_forward_returns", "compute_report", "information_coefficient"]
