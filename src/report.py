"""Build the HTML dashboard + TV import file + signals.csv (SPEC §8).

Primary output: a single-file, no-build HTML dashboard per timeframe with embedded
TradingView Advanced Chart widgets (free public embed; no API key; attribution kept
visible). Secondary: a ``.txt`` watchlist that imports into TradingView. Plus the
append-only ``signals.csv`` audit/calibration log.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

# signals.csv schema (SPEC §8.4). Bump SCHEMA_VERSION + migrate when this changes.
SCHEMA_VERSION = 1
SIGNALS_COLUMNS = (
    "run_ts", "ticker", "timeframe", "direction", "composite_score", "wyckoff_score",
    "range_score", "volume_score", "spring_score", "confirmation_score",
    "rs_vs_spy", "vol_contraction", "mtf_agree", "trend_context", "data_quality_flag",
    "feat_volume_ratio", "feat_volume_pctile", "feat_spread_atr", "feat_spread_pctile",
    "feat_close_position", "made_watchlist", "transition",
)


def render_dashboard(cards: list[dict[str, Any]], timeframe: str, config: Any) -> Path:
    """Render ``report_<tf>_<date>.html`` + refresh ``latest_<tf>.html``. Returns the path."""
    raise NotImplementedError


def write_tv_import_file(cards: list[dict[str, Any]], timeframe: str, config: Any) -> Path:
    """Write the secondary TV ``.txt`` watchlist (one exchange-prefixed symbol per line)."""
    raise NotImplementedError


def append_signals(rows: list[dict[str, Any]], path: Path) -> None:
    """Append one row per evaluated ticker to ``signals.csv`` (schema ``SIGNALS_COLUMNS``)."""
    raise NotImplementedError
