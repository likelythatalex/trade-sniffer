"""Build the HTML dashboard + TV import file + signals.csv (SPEC §8).

Primary output: a single-file, no-build HTML dashboard per timeframe with embedded
TradingView Advanced Chart widgets (free public embed; no API key; attribution kept
visible; lazy-loaded). Secondary: a ``.txt`` watchlist that imports into TradingView.
Plus the append-only ``signals.csv`` audit/calibration log.

This module owns *presentation*: the scanner hands it plain card dicts
(``ticker, exchange, direction, score, sub_scores, reasons``) and report.py builds
the TV symbol, ranks, and renders.
"""
from __future__ import annotations

import csv
import shutil
from datetime import date
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import Config

# signals.csv schema (SPEC §8.4). Bump SCHEMA_VERSION + migrate when this changes.
SCHEMA_VERSION = 1
SIGNALS_COLUMNS = (
    "run_ts", "ticker", "timeframe", "direction", "composite_score", "wyckoff_score",
    "range_score", "volume_score", "spring_score", "confirmation_score",
    "rs_vs_spy", "vol_contraction", "mtf_agree", "trend_context", "data_quality_flag",
    "feat_volume_ratio", "feat_volume_pctile", "feat_spread_atr", "feat_spread_pctile",
    "feat_close_position", "made_watchlist", "transition",
)

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
_TEMPLATE_NAME = "report.html.j2"

# yfinance exchange name (normalized: upper-cased, spaces stripped) -> TradingView code.
_TV_EXCHANGE = {
    "NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ",
    "NASDAQGS": "NASDAQ", "NASDAQGM": "NASDAQ", "NASDAQCM": "NASDAQ", "NASDAQ": "NASDAQ",
    "NYQ": "NYSE", "NYSE": "NYSE", "NEWYORKSTOCKEXCHANGE": "NYSE",
    "PCX": "AMEX", "ASE": "AMEX", "AMEX": "AMEX",
    "NYSEARCA": "AMEX", "NYSEAMERICAN": "AMEX", "BATS": "AMEX",
}


def render_dashboard(
    cards: list[dict[str, Any]],
    timeframe: str,
    config: Config,
    today: date | None = None,
    summary: dict[str, Any] | None = None,
) -> Path:
    """Render ``report_<tf>_<date>.html`` + refresh ``latest_<tf>.html``. Returns the path.

    Cards are split into accumulation/distribution and ranked by score (descending)
    within each. Each card gets a TV symbol for its embedded chart.
    """
    today = today or date.today()
    accumulation = _ranked_view(cards, "accumulation")
    distribution = _ranked_view(cards, "distribution")

    environment = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )
    html = environment.get_template(_TEMPLATE_NAME).render(
        title=config.output.report_title,
        timeframe=timeframe,
        generated_ts=today.isoformat(),
        theme=config.output.theme,
        interval=config.output.embed_chart_interval[timeframe],
        summary=summary or {},
        accumulation=accumulation,
        distribution=distribution,
    )

    output_dir = Path(config.output.dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"report_{timeframe}_{today.isoformat()}.html"
    report_path.write_text(html, encoding="utf-8")
    shutil.copyfile(report_path, output_dir / f"latest_{timeframe}.html")
    return report_path


def write_tv_import_file(cards: list[dict[str, Any]], timeframe: str, config: Config) -> Path:
    """Write the secondary TV ``.txt`` watchlist (one exchange-prefixed symbol per line),
    grouped by direction with ``###`` section markers."""
    accumulation = _ranked_view(cards, "accumulation")
    distribution = _ranked_view(cards, "distribution")

    lines: list[str] = []
    if accumulation:
        lines.append("###ACCUMULATION")
        lines.extend(card["symbol"] for card in accumulation)
    if distribution:
        lines.append("###DISTRIBUTION")
        lines.extend(card["symbol"] for card in distribution)

    output_dir = Path(config.output.dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"watchlist_{timeframe}.txt"
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return path


def append_signals(rows: list[dict[str, Any]], path: Path) -> None:
    """Append one row per evaluated ticker to ``signals.csv`` (schema ``SIGNALS_COLUMNS``).

    Writes the header only when creating the file. Unknown keys are ignored and
    missing columns default to empty, so the log stays schema-stable.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SIGNALS_COLUMNS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in SIGNALS_COLUMNS})


# --- helpers ------------------------------------------------------------------


def _ranked_view(cards: list[dict[str, Any]], direction: str) -> list[dict[str, Any]]:
    """Cards of one direction, ranked by score descending, with a TV symbol added."""
    selected = [card for card in cards if card.get("direction") == direction]
    selected.sort(key=lambda card: card.get("score", 0.0), reverse=True)
    return [{**card, "symbol": _to_tv_symbol(card["ticker"], card.get("exchange"))} for card in selected]


def _to_tv_symbol(ticker: str, exchange: str | None) -> str:
    """Build a TradingView symbol (``EXCHANGE:TICKER``), falling back to the bare
    ticker if the exchange can't be mapped (TV often still resolves it)."""
    if not exchange:
        return ticker
    code = _TV_EXCHANGE.get(exchange.upper().replace(" ", ""))
    return f"{code}:{ticker}" if code else ticker
