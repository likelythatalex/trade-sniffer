"""Per-stock normalization → relative features (SPEC §5A). Pure, strategy-agnostic.

Every Wyckoff "high/low volume" or "narrow/wide spread" call is relative to each
stock's own rolling distribution, never an absolute dollar/share figure. This pass
turns a cleaned OHLCV frame into the relative-feature frame the strategy consumes,
so it is reusable by any future strategy.

Degenerate bars (zero range, zero rolling ATR/median) emit **NaN**, not a coerced
value; the strategy treats NaN as "no signal" and never lets it propagate (§6.4).
"""
from __future__ import annotations

import pandas as pd


def compute_features(df: pd.DataFrame, baseline_window: int) -> pd.DataFrame:
    """Compute the relative-feature frame, aligned to ``df``'s index.

    Columns produced (rolling over ``baseline_window``):

    - ``volume_ratio``   — bar volume ÷ rolling **median** volume (median, not
      mean: volume is right-skewed and one climax bar would inflate a mean).
    - ``volume_pctile``  — percentile rank of bar volume within the window.
    - ``spread_atr``     — bar range (high − low) ÷ rolling ATR.
    - ``spread_pctile``  — percentile rank of bar range within the window.
    - ``close_position`` — (close − low) ÷ (high − low); where the close landed.

    Bars with < ``baseline_window`` of preceding history are left undefined (not
    scored — covered by the §5.1 warmup rule).
    """
    raise NotImplementedError
