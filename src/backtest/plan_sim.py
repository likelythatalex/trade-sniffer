"""Plan-outcome simulator + policy sweep (Tier 3) — tune the trade planner offline.

Part (1), the path-dependent evaluator, already exists: ``trade_outcome.evaluate_outcome``
(walk forward, resolve stop-vs-target-first → realized R). This is part (2): replay history
**once** to collect every directional signal's structural ``Levels`` + forward OHLC, then for a
given ``TradePlanConfig`` run ``plan_trade`` → ``evaluate_outcome`` to a realized R per trade and
aggregate the R distribution. Because ``plan_trade`` is *pure-on-config*, we can **sweep** many
configs over the same collected trials cheaply and compare policies apples-to-apples (esp.
``stop_method`` / ``max_stop_pct`` — the reward:risk lever).

Faithful to the planner's design: the entry is the *breakout* level, so a trade only counts if
price actually triggers it within the forward window — a non-triggering breakout is ``no_fill``,
never a fabricated loss. Fills resolve from the bar *after* the trigger (no entry-bar noise).

**Discipline (this is calibration — treat it as such):** replay is **survivorship-biased**
(today's universe), samples **overlap** (raise ``--step``), and a full-sample sweep is
*in-sample* — so prefer robust **plateaus** over single peaks, validate **out-of-sample**
(``--oos-frac``), and use the live private journal for **absolute** expectancy. This ranks
policies; it does not bless one.
"""
from __future__ import annotations

import argparse
import dataclasses
import itertools
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import Config, TradePlanConfig, load_config
from ..data import fetch_many
from ..trade_outcome import evaluate_outcome
from ..trade_plan import plan_trade
from ..universe import load_universe
from .replay import benchmark_close, score_history

logger = logging.getLogger(__name__)

_RESULTS_DIR = Path("backtest_results")
_ACCUM, _DIST = "accumulation", "distribution"
_TO_TRADE_DIR = {_ACCUM: "long", _DIST: "short"}  # planner vocab → trade_outcome vocab


@dataclass(frozen=True)
class Trial:
    """One directional signal to be planned + resolved: the structural levels the strategy
    saw, plus the OHLC bars *after* the as-of bar (the path the plan plays out against)."""

    ticker: str
    date: Any
    direction: str  # accumulation | distribution
    levels: Any     # strategies.base.Levels
    forward: pd.DataFrame


def collect_trials(
    tickers: list[str], timeframe: str, config: Config, today: date | None = None, step: int = 1
) -> list[Trial]:
    """Replay history once and collect a ``Trial`` per directional signal (levels + forward
    bars). Scored with the same benchmark the production pipeline uses, so the signals match."""
    today = today or date.today()
    fetched = fetch_many(tickers, timeframe, config, today=today)
    benchmark = benchmark_close(timeframe, config, today)
    trials: list[Trial] = []
    for ticker, i, cleaned, composite in score_history(fetched, benchmark, timeframe, config, step):
        if composite.direction not in (_ACCUM, _DIST):
            continue
        forward = cleaned.iloc[i + 1 :]
        if forward.empty:
            continue  # signal on the last bar — no path to resolve against
        trials.append(Trial(ticker, cleaned.index[i], composite.direction, composite.levels, forward))
    return trials


def simulate(trials: list[Trial], plan_cfg: TradePlanConfig) -> pd.DataFrame:
    """Resolve every trial under ``plan_cfg`` → one outcome row each.

    Columns: ticker, date, direction, reward_risk, filled, resolution
    (``no_fill``/``target``/``stop``/``open``), realized_r, mfe_r, mae_r, bars_held.
    Trials the planner abstains on (no plannable levels) are dropped — they're not trades.
    """
    records: list[dict[str, Any]] = []
    for trial in trials:
        plan = plan_trade(trial.direction, trial.levels, plan_cfg)
        if plan is None:
            continue  # planner abstained — nothing to resolve
        records.append(_resolve(trial, plan))
    return pd.DataFrame.from_records(records)


def _resolve(trial: Trial, plan: Any) -> dict[str, Any]:
    """Resolve one planned trial: model the breakout trigger, then the stop/target race."""
    base = {
        "ticker": trial.ticker, "date": trial.date, "direction": trial.direction,
        "reward_risk": plan.reward_risk,
    }
    unfilled = {**base, "filled": False, "resolution": "no_fill", "realized_r": None,
                "mfe_r": float("nan"), "mae_r": float("nan"), "bars_held": 0}

    fill = _first_fill(trial.direction, plan.entry, trial.forward)
    if fill is None:
        return unfilled  # the breakout never triggered within the window
    post_fill = trial.forward.iloc[fill + 1 :]  # resolve from the bar AFTER the trigger
    outcome = evaluate_outcome(_TO_TRADE_DIR[trial.direction], plan.entry, plan.stop, plan.target, post_fill)
    if outcome is None:  # filled on the last available bar — no path to resolve
        return {**base, "filled": True, "resolution": "open", "realized_r": None,
                "mfe_r": float("nan"), "mae_r": float("nan"), "bars_held": 0}
    return {
        **base, "filled": True, "resolution": outcome.resolution, "realized_r": outcome.realized_r,
        "mfe_r": outcome.mfe_r, "mae_r": outcome.mae_r, "bars_held": outcome.bars_held,
    }


def _first_fill(direction: str, entry: float, forward: pd.DataFrame) -> int | None:
    """Index of the first forward bar that triggers the breakout entry (long: high ≥ entry;
    short: low ≤ entry), or ``None`` if it never triggers within the window."""
    is_long = direction == _ACCUM
    highs = forward["high"].to_numpy(dtype=float)
    lows = forward["low"].to_numpy(dtype=float)
    for idx in range(len(forward)):
        if (highs[idx] >= entry) if is_long else (lows[idx] <= entry):
            return idx
    return None


def summarize_policy(outcomes: pd.DataFrame) -> dict[str, Any]:
    """Aggregate one policy's per-trade outcomes into the headline R-distribution stats.

    ``expectancy_r`` (mean realized R over *resolved* trades) is the bottom line; ``fill_rate``
    and ``open_rate`` flag whether the breakout triggers and whether the window was long enough
    to resolve. R is self-normalizing, so this is comparable across stocks and policies.
    """
    n = int(len(outcomes))
    if n == 0:
        return {"n_trials": 0, "fill_rate": float("nan"), "n_resolved": 0, "win_rate": float("nan"),
                "expectancy_r": float("nan"), "avg_win_r": float("nan"), "avg_loss_r": float("nan"),
                "profit_factor": float("nan"), "target_rate": float("nan"), "stop_rate": float("nan"),
                "open_rate": float("nan"), "median_mfe_r": float("nan"), "median_mae_r": float("nan"),
                "median_bars_held": float("nan")}

    filled = outcomes[outcomes["filled"]]
    resolved = filled[filled["realized_r"].notna()]
    wins = resolved[resolved["realized_r"] > 0]
    losses = resolved[resolved["realized_r"] < 0]
    gross_win = float(wins["realized_r"].sum())
    gross_loss = float(-losses["realized_r"].sum())
    return {
        "n_trials": n,
        "fill_rate": len(filled) / n,
        "n_resolved": int(len(resolved)),
        "win_rate": len(wins) / len(resolved) if len(resolved) else float("nan"),
        "expectancy_r": float(resolved["realized_r"].mean()) if len(resolved) else float("nan"),
        "avg_win_r": float(wins["realized_r"].mean()) if len(wins) else float("nan"),
        "avg_loss_r": float(losses["realized_r"].mean()) if len(losses) else float("nan"),
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else float("nan"),
        "target_rate": float((filled["resolution"] == "target").mean()) if len(filled) else float("nan"),
        "stop_rate": float((filled["resolution"] == "stop").mean()) if len(filled) else float("nan"),
        "open_rate": float((filled["resolution"] == "open").mean()) if len(filled) else float("nan"),
        "median_mfe_r": float(filled["mfe_r"].median()) if len(filled) else float("nan"),
        "median_mae_r": float(filled["mae_r"].median()) if len(filled) else float("nan"),
        "median_bars_held": float(resolved["bars_held"].median()) if len(resolved) else float("nan"),
    }


def sweep(
    trials: list[Trial], base_cfg: TradePlanConfig, grid: dict[str, list], oos_frac: float = 0.0
) -> pd.DataFrame:
    """Run the cartesian product of ``grid`` (e.g. ``stop_method`` × ``max_stop_pct``) over the
    same ``trials``, one summary row per policy, sorted by in-sample expectancy.

    When ``oos_frac`` > 0, trials are split by date into an earlier in-sample slice (where the
    sweep is scored) and a held-out out-of-sample tail; each row gains ``oos_expectancy_r`` /
    ``oos_n_resolved`` so you can check the in-sample winner *holds* (the anti-overfit test).
    """
    keys = list(grid)
    in_sample, out_sample = _split_by_date(trials, oos_frac)
    rows: list[dict[str, Any]] = []
    for combo in itertools.product(*(grid[k] for k in keys)):
        cfg = dataclasses.replace(base_cfg, **dict(zip(keys, combo)))
        row = {**dict(zip(keys, combo)), **summarize_policy(simulate(in_sample, cfg))}
        if out_sample is not None:
            oos = summarize_policy(simulate(out_sample, cfg))
            row["oos_expectancy_r"] = oos["expectancy_r"]
            row["oos_n_resolved"] = oos["n_resolved"]
        rows.append(row)
    df = pd.DataFrame(rows)
    return df.sort_values("expectancy_r", ascending=False, na_position="last").reset_index(drop=True)


def _split_by_date(trials: list[Trial], oos_frac: float) -> tuple[list[Trial], list[Trial] | None]:
    """Chronological train/test split: earliest ``1-oos_frac`` in-sample, latest tail held out.
    ``oos_frac <= 0`` → no split (full sample in-sample, no held-out set)."""
    if oos_frac <= 0 or len(trials) < 2:
        return trials, None
    ordered = sorted(trials, key=lambda t: t.date)
    cut = int(len(ordered) * (1.0 - oos_frac))
    return ordered[:cut], ordered[cut:]


# --- report -------------------------------------------------------------------

_CAVEAT = (
    "> **Read this first — it's calibration, not a verdict.** Replay scores *today's* universe, "
    "so results carry **survivorship bias**; overlapping samples inflate counts (raise `--step`); "
    "and a full-sample sweep is **in-sample** (overfit risk). Prefer a robust **plateau** of "
    "configs over a single peak, validate **out-of-sample** (`--oos-frac`), and trust the live "
    "private journal for **absolute** expectancy. R is self-normalizing — comparable across "
    "policies, but it never executes a trade."
)


def render_markdown(sweep_df: pd.DataFrame, base_cfg: TradePlanConfig, timeframe: str, today: str, oos_frac: float) -> str:
    has_oos = "oos_expectancy_r" in sweep_df.columns
    header = ["stop_method", "max_stop_pct", "expectancy_r", "win_rate", "profit_factor",
              "fill_rate", "target/stop/open", "med MFE_r", "med MAE_r", "n_resolved"]
    if has_oos:
        header.insert(3, "oos_exp_r")
    lines = [
        f"# Plan-outcome policy sweep — {timeframe} — {today}",
        "",
        _CAVEAT,
        "",
        f"Base plan config: stop_method=`{base_cfg.stop_method}`, max_stop_pct={base_cfg.max_stop_pct}, "
        f"stop_buffer_pct={base_cfg.stop_buffer_pct}, risk_pct={base_cfg.risk_pct}. "
        + (f"Out-of-sample tail: **{oos_frac:.0%}** (held out)." if has_oos else "Full-sample (no holdout)."),
        "",
        "Sorted by in-sample `expectancy_r` (mean realized R per resolved trade). "
        "**Look for a plateau, not the single top row.**",
        "",
        "| " + " | ".join(header) + " |",
        "|" + "---|" * len(header),
    ]
    for _, r in sweep_df.iterrows():
        cells = [
            _g(r.get("stop_method")), _g(r.get("max_stop_pct")), _r(r.get("expectancy_r")),
            _pct(r.get("win_rate")), _r(r.get("profit_factor")), _pct(r.get("fill_rate")),
            f"{_pct(r.get('target_rate'))}/{_pct(r.get('stop_rate'))}/{_pct(r.get('open_rate'))}",
            _r(r.get("median_mfe_r")), _r(r.get("median_mae_r")), _g(int(r.get("n_resolved", 0))),
        ]
        if has_oos:
            cells.insert(3, _r(r.get("oos_expectancy_r")))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def render_report(sweep_df: pd.DataFrame, base_cfg: TradePlanConfig, output_dir: Path, timeframe: str, oos_frac: float) -> Path:
    """Write ``policy_sweep_<tf>_<date>.md`` (table) + ``..._rows.csv`` (the sweep). Returns the .md path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    md_path = output_dir / f"policy_sweep_{timeframe}_{today}.md"
    csv_path = output_dir / f"policy_sweep_{timeframe}_{today}_rows.csv"
    sweep_df.to_csv(csv_path, index=False)
    md_path.write_text(render_markdown(sweep_df, base_cfg, timeframe, today, oos_frac), encoding="utf-8")
    return md_path


def _r(value: Any) -> str:
    return "n/a" if value is None or pd.isna(value) else f"{float(value):+.2f}"


def _pct(value: Any) -> str:
    return "n/a" if value is None or pd.isna(value) else f"{float(value) * 100:.0f}%"


def _g(value: Any) -> str:
    return "n/a" if value is None or (isinstance(value, float) and pd.isna(value)) else f"{value}"


# --- CLI ----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Plan-outcome simulator + policy sweep (offline replay).")
    parser.add_argument("--timeframe", choices=["daily", "weekly"], default="daily")
    parser.add_argument("--tickers", help="comma-separated tickers (default: universe.txt)")
    parser.add_argument("--limit", type=int, help="cap the universe to the first N tickers (speed)")
    parser.add_argument("--step", type=int, default=5, help="score every Nth bar (default 5 — fewer overlapping trades)")
    parser.add_argument("--stop-methods", default="capped,structural,atr", help="stop_method values to sweep")
    parser.add_argument("--max-stop-pcts", default="5,8,12", help="max_stop_pct values to sweep (used by the capped method)")
    parser.add_argument("--oos-frac", type=float, default=0.0, help="hold out this fraction (by date) as out-of-sample")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    config = load_config()
    tickers = _resolve_tickers(args, config)
    logger.info("collecting trials: %d tickers on %s (step=%d)", len(tickers), args.timeframe, args.step)
    trials = collect_trials(tickers, args.timeframe, config, step=args.step)
    if not trials:
        logger.warning("no directional signals produced; nothing to sweep.")
        return

    grid = {
        "stop_method": [s.strip() for s in args.stop_methods.split(",") if s.strip()],
        "max_stop_pct": [float(p) for p in args.max_stop_pcts.split(",") if p.strip()],
    }
    logger.info("sweeping %d policies over %d trials", len(grid["stop_method"]) * len(grid["max_stop_pct"]), len(trials))
    sweep_df = sweep(trials, config.trade_plan, grid, oos_frac=args.oos_frac)
    path = render_report(sweep_df, config.trade_plan, _RESULTS_DIR, args.timeframe, args.oos_frac)
    logger.info("policy sweep written: %s", path)


def _resolve_tickers(args: argparse.Namespace, config: Config) -> list[str]:
    if args.tickers:
        return [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    universe = load_universe(Path(config.universe_file))
    return universe[: args.limit] if args.limit else universe


if __name__ == "__main__":
    main()
