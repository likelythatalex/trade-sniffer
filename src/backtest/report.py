"""Render the backtest report — markdown summary + the raw enriched rows as CSV.

Deliberately tabular (no plotting dependency). The survivorship caveat is printed at the
top of every report so a replay result is never mistaken for an unbiased one.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

_CAVEAT = (
    "> **Replay caveat:** scores are re-computed over *today's* universe, so results carry "
    "**survivorship bias** (only companies that survived to today are scored) and MTF "
    "agreement is not replayed. Use for calibration/iteration, not as an unbiased verdict. "
    "The unbiased path is analysing accumulated live `signals.csv` over time."
)


def render_report(report: dict[str, Any], enriched: pd.DataFrame, output_dir: Path, timeframe: str) -> Path:
    """Write ``backtest_<tf>_<date>.md`` (summary) + ``..._rows.csv`` (raw). Returns the .md path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    md_path = output_dir / f"backtest_{timeframe}_{today}.md"
    csv_path = output_dir / f"backtest_{timeframe}_{today}_rows.csv"

    enriched.to_csv(csv_path, index=False)
    md_path.write_text(_render_markdown(report, timeframe, today, csv_path.name), encoding="utf-8")
    return md_path


def _render_markdown(report: dict[str, Any], timeframe: str, today: str, csv_name: str) -> str:
    lines = [
        f"# Backtest — {timeframe} — {today}",
        "",
        _CAVEAT,
        "",
        f"Signals scored: **{report['n_signals']:,}**  ·  ranking column: `{report['score_col']}`  ·  "
        f"raw rows: `{csv_name}`",
        "",
        "**IC** = Spearman rank correlation between the signed score and forward *excess* "
        "return vs SPY (the headline: do higher scores rank better outcomes?). "
        "Positive + rising-with-bucket = the score is informative.",
        "",
    ]
    for horizon, h in report["horizons"].items():
        lines += [
            f"## Horizon: {horizon} bars",
            "",
            f"- Signals with an outcome: **{h['n_with_outcome']:,}**",
            f"- **IC (excess vs SPY): {_fmt(h['ic_excess'])}**   ·   IC (raw return): {_fmt(h['ic_raw'])}",
            "",
            _hit_rate_block(h["hit_rate"]),
            "",
            "### Excess return by signed-score bucket (monotonic = good)",
            "",
            _bucket_table(h["buckets"]),
            "",
            "### Per-sub-score IC (excess) — which sub-scores carry predictive weight",
            "",
            _subscore_table(h["subscore_ic"]),
            "",
        ]
    return "\n".join(lines)


def _hit_rate_block(hr: dict[str, float]) -> str:
    if not hr:
        return "_No directional outcomes to score._"
    return (
        f"- Accumulation hit rate {_pct(hr['accumulation_hit_rate'])} vs base {_pct(hr['base_rate_up'])} "
        f"→ **lift {_pct(hr['accumulation_lift'])}** (n={hr['accumulation_n']:,})\n"
        f"- Distribution hit rate {_pct(hr['distribution_hit_rate'])} vs base {_pct(hr['base_rate_down'])} "
        f"→ **lift {_pct(hr['distribution_lift'])}** (n={hr['distribution_n']:,})"
    )


def _bucket_table(buckets: pd.DataFrame) -> str:
    if buckets.empty:
        return "_No data._"
    lines = ["| score bucket | mean excess | median excess | count |", "|---|---|---|---|"]
    for _, row in buckets.iterrows():
        lines.append(f"| {row['bucket']} | {_pct(row['mean'])} | {_pct(row['median'])} | {int(row['count']):,} |")
    return "\n".join(lines)


def _subscore_table(subscore_ic: dict[str, float]) -> str:
    if not subscore_ic:
        return "_No sub-scores._"
    lines = ["| sub-score | IC (excess) |", "|---|---|"]
    for name, ic in sorted(subscore_ic.items(), key=lambda kv: (float("-inf") if pd.isna(kv[1]) else kv[1]), reverse=True):
        lines.append(f"| `{name.removeprefix('sub_')}` | {_fmt(ic)} |")
    return "\n".join(lines)


def _fmt(value: float) -> str:
    return "n/a" if value is None or pd.isna(value) else f"{value:+.3f}"


def _pct(value: float) -> str:
    return "n/a" if value is None or pd.isna(value) else f"{value * 100:+.2f}%"
