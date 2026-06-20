"""Backtest metrics — is the score informative? (Tier 3). Pure functions.

The headline is the **Information Coefficient (IC)**: the Spearman rank correlation
between the SIGNED score and forward EXCESS return. That's the right metric for a
*ranking* conviction score — it asks "do higher scores rank better outcomes?", not
"did one threshold work". Plus by-bucket returns (monotonicity), hit-rate lift over the
base rate, and the same IC per sub-score (which sub-scores carry predictive weight →
direct input to calibration). Spearman is computed via pandas (no scipy dependency).
"""
from __future__ import annotations

from typing import Any

import pandas as pd

# Signed-score bucket edges (-100 distribution … +100 accumulation). The tails are what
# matter for a conviction score; the middle holds low-conviction/no-range rows.
DEFAULT_BUCKETS = (-100, -80, -60, -40, -20, 0, 20, 40, 60, 80, 100)


def information_coefficient(df: pd.DataFrame, score_col: str, outcome_col: str) -> float:
    """Spearman rank correlation between ``score_col`` and ``outcome_col`` (NaN-safe).

    Computed as Pearson correlation of the ranks (identical to Spearman) so we don't
    pull in scipy, which pandas' ``method="spearman"`` would require. Returns NaN if
    there aren't enough varying pairs to rank — never raises.
    """
    if score_col not in df or outcome_col not in df:
        return float("nan")
    pair = df[[score_col, outcome_col]].dropna()
    if len(pair) < 3 or pair[score_col].nunique() < 2 or pair[outcome_col].nunique() < 2:
        return float("nan")
    return float(pair[score_col].rank().corr(pair[outcome_col].rank()))


def bucketed_returns(
    df: pd.DataFrame, score_col: str, outcome_col: str, buckets: tuple[int, ...] = DEFAULT_BUCKETS
) -> pd.DataFrame:
    """Mean/median/count of ``outcome_col`` per signed-score bucket. A good conviction
    score is monotonic — higher buckets should show higher outcomes."""
    pair = df[[score_col, outcome_col]].dropna()
    columns = ["bucket", "mean", "median", "count"]
    if pair.empty:
        return pd.DataFrame(columns=columns)
    cats = pd.cut(pair[score_col], bins=list(buckets))
    grouped = pair.groupby(cats, observed=True)[outcome_col]
    out = pd.DataFrame({"mean": grouped.mean(), "median": grouped.median(), "count": grouped.size()})
    out = out.reset_index()
    out.columns = columns
    return out


def hit_rate_lift(df: pd.DataFrame, outcome_col: str, score_col: str) -> dict[str, float]:
    """Directional hit rates vs the base rate of the move.

    For accumulation flags (signed > 0): hit = outcome > 0. For distribution
    (signed < 0): hit = outcome < 0. Lift = hit rate − base rate of that move across all
    scored rows. Lift, not raw hit rate, is what shows the signal adds information.
    """
    valid = df[[outcome_col, score_col]].dropna()
    if valid.empty:
        return {}
    base_up = float((valid[outcome_col] > 0).mean())
    base_down = float((valid[outcome_col] < 0).mean())

    accum = valid[valid[score_col] > 0]
    dist = valid[valid[score_col] < 0]
    accum_hit = float((accum[outcome_col] > 0).mean()) if len(accum) else float("nan")
    dist_hit = float((dist[outcome_col] < 0).mean()) if len(dist) else float("nan")

    return {
        "base_rate_up": base_up,
        "base_rate_down": base_down,
        "accumulation_hit_rate": accum_hit,
        "accumulation_lift": accum_hit - base_up,
        "accumulation_n": int(len(accum)),
        "distribution_hit_rate": dist_hit,
        "distribution_lift": dist_hit - base_down,
        "distribution_n": int(len(dist)),
    }


def compute_report(
    df: pd.DataFrame,
    horizons: list[int],
    score_col: str = "signed_score",
    subscore_prefix: str = "sub_",
    buckets: tuple[int, ...] = DEFAULT_BUCKETS,
) -> dict[str, Any]:
    """Assemble the full metrics structure across horizons. Iterates over whatever
    ``sub_*`` columns exist, so new strategies/sub-scores appear automatically."""
    subscore_cols = [c for c in df.columns if c.startswith(subscore_prefix)]
    report: dict[str, Any] = {"n_signals": int(len(df)), "score_col": score_col, "horizons": {}}
    for h in horizons:
        excess = f"excess_return_{h}"
        raw = f"fwd_return_{h}"
        report["horizons"][h] = {
            "n_with_outcome": int(df[excess].notna().sum()) if excess in df else 0,
            "ic_excess": information_coefficient(df, score_col, excess),
            "ic_raw": information_coefficient(df, score_col, raw),
            "buckets": bucketed_returns(df, score_col, excess, buckets),
            "hit_rate": hit_rate_lift(df, excess, score_col),
            "subscore_ic": {c: information_coefficient(df, c, excess) for c in subscore_cols},
        }
    return report
