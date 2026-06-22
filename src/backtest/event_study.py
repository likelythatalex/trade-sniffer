"""Failed→revived event study (Tier 3) — do second-chance setups eventually fulfil?

A *path-dependent* question the core IC harness can't answer: when a setup flags, invalidates,
then **re-qualifies** (a "revived" episode), does it tend to work out — or is a re-flag a
warning sign? Fixed-horizon IC averages over a window and can't see a dip-then-trigger; this
walks the forward price path and measures the **maximum favorable / adverse excursion** and
time-to-target per episode, then compares the *revived* cohort against *first-time* flags.

**Offline + point-in-time (the honest counterpart to replay).** It reconstructs everything from
the accumulated live ``signals.csv`` — episodes from ``episodes.py``, and the forward price path
from the ``close`` logged each run (schema v3). So unlike ``backtest/replay.py`` it carries **no
survivorship bias** (the log is point-in-time by construction) and needs no network and no schema
change. The cost is that excursions are **close-based** — they use the logged closes, not
intrabar highs/lows, so they *under*-state the true MFE/MAE. (True-OHLC, plan-resolved outcomes
are the separate future "plan-outcome simulator", which reuses ``trade_outcome.evaluate_outcome``.)

It is **data-gated**: the tooling is built now, but meaningful numbers need accrued history with
actual fail→revive episodes *and* enough forward runs after each revival. Run it as that accrues.
"""
from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from ..episodes import reconstruct_episodes
from ..report import read_signals

logger = logging.getLogger(__name__)

_RESULTS_DIR = Path("backtest_results")
_SIGN = {"accumulation": 1.0, "distribution": -1.0}


@dataclass(frozen=True)
class Excursion:
    """Close-path outcome of one episode, measured from its entry (revival/flag) close.

    Attributes:
        mfe: max favorable excursion as a fraction (≥ 0; direction-adjusted).
        mae: max adverse excursion as a fraction (≥ 0).
        n_forward: forward closes evaluated after entry.
        reached_target: did MFE reach the target within the path?
        bars_to_target: forward runs until the target was first reached (1-based), else ``None``.
    """

    mfe: float
    mae: float
    n_forward: int
    reached_target: bool
    bars_to_target: int | None


def close_path_excursion(
    entry: float, forward_closes: list[float], sign: float, target: float
) -> Excursion:
    """Walk the forward closes, tracking favorable/adverse excursion (mirrors the MFE/MAE
    convention of ``trade_outcome`` but close-based and in %, since the log has no OHLC/stop).

    ``sign`` is +1 for accumulation (favorable = up) / −1 for distribution (favorable = down).
    Favorable/adverse are clamped at 0, so both are ≥ 0 even if price only ever went one way.
    """
    mfe = mae = 0.0
    bars_to_target: int | None = None
    for i, close in enumerate(forward_closes, start=1):
        favorable = sign * (close - entry) / entry
        mfe = max(mfe, favorable)
        mae = max(mae, -favorable)
        if bars_to_target is None and favorable >= target:
            bars_to_target = i
    return Excursion(mfe, mae, len(forward_closes), bars_to_target is not None, bars_to_target)


def build_excursions(
    rows: list[dict], timeframe: str, target: float, max_horizon: int
) -> pd.DataFrame:
    """One row per episode (across all tickers) with its cohort + close-path excursion.

    Cohort is ``first`` for a ticker's first-ever episode on the timeframe and ``revived`` for
    every later one (each preceded by a since-invalidated episode — the failed→revived cohort).
    Episodes with no forward closes after entry (e.g. a revival on the latest logged run) are
    dropped — there's nothing to measure yet. Columns: ticker, direction, cohort, episode_index,
    entry_ts, entry_close, n_forward, mfe, mae, reached_target, bars_to_target.
    """
    closes = _closes_by_ticker(rows, timeframe)
    records: list[dict[str, Any]] = []
    for ticker in sorted({r.get("ticker") for r in rows if r.get("timeframe") == timeframe}):
        path = closes.get(ticker, [])
        if not path:
            continue
        position = {ts: i for i, (ts, _) in enumerate(path)}
        for index, episode in enumerate(reconstruct_episodes(rows, ticker, timeframe)):
            start = position.get(episode.start_ts)
            if start is None:  # entry bar had no valid close logged — can't anchor the path
                continue
            entry_close = path[start][1]
            forward = [c for _, c in path[start + 1 : start + 1 + max_horizon]]
            if not forward:
                continue
            sign = _SIGN.get(episode.direction, 0.0)
            if sign == 0.0:  # direction "none" never makes the watchlist, but stay defensive
                continue
            ex = close_path_excursion(entry_close, forward, sign, target)
            records.append({
                "ticker": ticker,
                "direction": episode.direction,
                "cohort": "first" if index == 0 else "revived",
                "episode_index": index,
                "entry_ts": episode.start_ts,
                "entry_close": entry_close,
                "n_forward": ex.n_forward,
                "mfe": ex.mfe,
                "mae": ex.mae,
                "reached_target": ex.reached_target,
                "bars_to_target": ex.bars_to_target,
            })
    return pd.DataFrame.from_records(records)


def summarize(excursions: pd.DataFrame, target: float, max_horizon: int, timeframe: str) -> dict[str, Any]:
    """Aggregate the per-episode excursions into the comparison structure (the headline is
    ``first`` vs ``revived``), plus a within-revived direction split for context."""
    revived = excursions[excursions["cohort"] == "revived"] if not excursions.empty else excursions
    summary: dict[str, Any] = {
        "timeframe": timeframe,
        "target": target,
        "max_horizon": max_horizon,
        "n_episodes": int(len(excursions)),
        "cohorts": {
            "first": _aggregate(excursions[excursions["cohort"] == "first"]) if not excursions.empty else _aggregate(excursions),
            "revived": _aggregate(revived),
        },
        "revived_by_direction": {
            direction: _aggregate(revived[revived["direction"] == direction])
            for direction in ("accumulation", "distribution")
        } if not excursions.empty else {},
    }
    return summary


def _aggregate(subset: pd.DataFrame) -> dict[str, Any]:
    """Cohort aggregates. ``median_bars_to_target`` is over the episodes that reached it."""
    n = int(len(subset))
    if n == 0:
        return {"n": 0, "fulfilment_rate": float("nan"), "median_mfe": float("nan"),
                "median_mae": float("nan"), "median_bars_to_target": float("nan")}
    reached = subset[subset["reached_target"]]
    return {
        "n": n,
        "fulfilment_rate": float(subset["reached_target"].mean()),
        "median_mfe": float(subset["mfe"].median()),
        "median_mae": float(subset["mae"].median()),
        "median_bars_to_target": float(reached["bars_to_target"].median()) if len(reached) else float("nan"),
    }


# --- report -------------------------------------------------------------------

_CAVEAT = (
    "> **Read this first.** Excursions are **close-based** (the log stores closes, not intrabar "
    "highs/lows), so MFE/MAE *under*-state the true move. But this is **point-in-time** — "
    "reconstructed from the accumulated live `signals.csv`, so unlike the replay backtester it "
    "carries **no survivorship bias**. It is **data-gated**: results are only as meaningful as "
    "the accrued history of fail→revive episodes and the forward runs after each revival."
)


def render_markdown(summary: dict[str, Any], csv_name: str, today: str) -> str:
    """Markdown summary: the first-vs-revived comparison + a within-revived direction split."""
    tf, target, horizon = summary["timeframe"], summary["target"], summary["max_horizon"]
    lines = [
        f"# Failed→revived event study — {tf} — {today}",
        "",
        _CAVEAT,
        "",
        f"Episodes measured: **{summary['n_episodes']:,}**  ·  target **{target:+.0%}**  ·  "
        f"horizon **{horizon} runs**  ·  raw rows: `{csv_name}`",
        "",
        "**The question:** does a *revived* setup (re-flagged after invalidating) fulfil more or "
        "less than a *first-time* flag? Compare the two rows.",
        "",
        "| cohort | n | fulfilment (MFE ≥ target) | median MFE | median MAE | median runs→target |",
        "|---|---|---|---|---|---|",
        _cohort_row("first-time", summary["cohorts"]["first"]),
        _cohort_row("revived", summary["cohorts"]["revived"]),
        "",
        "### Revived cohort, by direction",
        "",
        "| direction | n | fulfilment | median MFE | median MAE | median runs→target |",
        "|---|---|---|---|---|---|",
    ]
    for direction, agg in summary.get("revived_by_direction", {}).items():
        lines.append(_cohort_row(direction, agg))
    return "\n".join(lines) + "\n"


def _cohort_row(label: str, agg: dict[str, Any]) -> str:
    return (
        f"| {label} | {agg['n']:,} | {_pct(agg['fulfilment_rate'])} | {_pct(agg['median_mfe'])} | "
        f"{_pct(agg['median_mae'])} | {_num(agg['median_bars_to_target'])} |"
    )


def render_report(summary: dict[str, Any], excursions: pd.DataFrame, output_dir: Path, timeframe: str) -> Path:
    """Write ``event_study_<tf>_<date>.md`` (summary) + ``..._rows.csv`` (per-episode). Returns the .md path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    md_path = output_dir / f"event_study_{timeframe}_{today}.md"
    csv_path = output_dir / f"event_study_{timeframe}_{today}_rows.csv"
    excursions.to_csv(csv_path, index=False)
    md_path.write_text(render_markdown(summary, csv_path.name, today), encoding="utf-8")
    return md_path


def _pct(value: float) -> str:
    return "n/a" if value is None or pd.isna(value) else f"{value * 100:+.1f}%"


def _num(value: float) -> str:
    return "n/a" if value is None or pd.isna(value) else f"{value:.0f}"


# --- helpers + CLI ------------------------------------------------------------


def _closes_by_ticker(rows: list[dict], timeframe: str) -> dict[str, list[tuple[str, float]]]:
    """Per ticker: the full ordered ``(run_ts, close)`` path on this timeframe (all evaluated
    runs, not just qualifying ones — price keeps moving after a setup drops off). Rows with no
    usable close are skipped so they can't anchor or distort the path."""
    paths: dict[str, list[tuple[str, float]]] = {}
    for row in rows:
        if row.get("timeframe") != timeframe or not row.get("run_ts"):
            continue
        close = _to_float(row.get("close"))
        if close is None or close <= 0:
            continue
        paths.setdefault(row["ticker"], []).append((row["run_ts"], close))
    for ticker in paths:
        paths[ticker].sort(key=lambda pair: pair[0])  # ISO run_ts sorts chronologically
    return paths


def _to_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Failed→revived event study (offline, from signals.csv).")
    parser.add_argument("--timeframe", choices=["daily", "weekly"], default="daily")
    parser.add_argument("--target", type=float, default=0.10, help="favorable-move target as a fraction (default 0.10)")
    parser.add_argument("--max-horizon", type=int, default=60, help="forward runs to measure per episode (default 60)")
    parser.add_argument("--signals", default="output/signals.csv", help="path to the signals log")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    rows = read_signals(Path(args.signals))
    if not rows:
        logger.warning("no signals at %s; nothing to study.", args.signals)
        return

    excursions = build_excursions(rows, args.timeframe, args.target, args.max_horizon)
    if excursions.empty:
        logger.warning("no measurable episodes for %s (need fail→revive history + forward runs).", args.timeframe)
        return

    summary = summarize(excursions, args.target, args.max_horizon, args.timeframe)
    path = render_report(summary, excursions, _RESULTS_DIR, args.timeframe)
    logger.info("event study written: %s", path)


if __name__ == "__main__":
    main()
