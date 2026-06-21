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
# append_signals() migrates an older-schema file in place, so bumps are additive-safe.
# v2: added momentum_score (the 2nd strategy's signed composite; additive — old rows blank).
# v3: added raw close/volume of the evaluated bar so forward outcomes are derivable from
#     the log itself (survivorship-honest, no re-fetch) instead of only via replay.
# v4: added news_sentiment_score (independent non-price signal; "" = no data, 0.0 = neutral).
# v5: added insider_score (Form 4 net buy/sell ratio; "" = no data, 0.0 = balanced).
SCHEMA_VERSION = 5
SIGNALS_COLUMNS = (
    "run_ts", "ticker", "timeframe", "direction", "composite_score", "wyckoff_score",
    "momentum_score", "news_sentiment_score", "insider_score",
    "range_score", "volume_score", "spring_score", "confirmation_score",
    "rs_vs_spy", "vol_contraction", "mtf_agree", "trend_context", "data_quality_flag",
    # Raw bar facts the feat_* are derived from — kept so the log alone yields forward outcomes.
    "close", "volume",
    "feat_volume_ratio", "feat_volume_pctile", "feat_spread_atr", "feat_spread_pctile",
    "feat_close_position", "made_watchlist", "transition",
)

# market.csv schema — the once-per-run, market-wide context (NOT per-ticker, so it gets its
# own file, not a signals.csv column). One row per run/timeframe.
MARKET_COLUMNS = (
    "run_ts", "timeframe", "regime", "spy_above_ma", "spy_distance_pct",
    "breadth_pct", "n_breadth", "ma_window",
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
    market: dict[str, Any] | None = None,
) -> Path:
    """Render ``report_<tf>_<date>.html`` + refresh ``latest_<tf>.html``. Returns the path.

    Cards are split into accumulation/distribution and ranked by score (descending)
    within each. Each card gets a TV symbol for its embedded chart.
    """
    today = today or date.today()
    accumulation = _ranked_view(cards, "accumulation")
    distribution = _ranked_view(cards, "distribution")
    # Per-ticker chart data + metadata, embedded as JSON for the single shared chart.
    cards_map = {card["ticker"]: card for card in accumulation + distribution}

    environment = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )
    html = environment.get_template(_TEMPLATE_NAME).render(
        title=config.output.report_title,
        timeframe=timeframe,
        generated_ts=today.isoformat(),
        theme=config.output.theme,
        summary=summary or {},
        market=market or {},
        accumulation=accumulation,
        distribution=distribution,
        cards_map=cards_map,
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


def write_index_page(config: Config) -> Path:
    """Write ``output/index.html`` — a tiny landing page linking the latest Daily and
    Weekly dashboards, so the bare GitHub Pages URL (``/``) resolves instead of 404ing.

    Static and link-only (the dashboards themselves host the TV widgets + attribution),
    so it's safe to overwrite every run and lists both timeframes regardless of which
    one just ran.
    """
    title = config.output.report_title
    links = "\n".join(
        f'      <li><a href="latest_{tf}.html">Latest {tf.capitalize()} dashboard</a></li>'
        for tf in ("daily", "weekly")
    )
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; background: #0e0f13; color: #e6e6e6;
           max-width: 40rem; margin: 4rem auto; padding: 0 1rem; line-height: 1.6; }}
    a {{ color: #5aa9ff; }}
    ul {{ list-style: none; padding: 0; }}
    li {{ margin: 0.5rem 0; font-size: 1.15rem; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <p>Flags Wyckoff accumulation/distribution candidates for human review &mdash; it never trades.</p>
  <ul>
{links}
  </ul>
</body>
</html>
"""
    output_dir = Path(config.output.dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "index.html"
    path.write_text(html, encoding="utf-8")
    return path


def append_signals(rows: list[dict[str, Any]], path: Path) -> None:
    """Append one row per evaluated ticker to ``signals.csv`` (schema ``SIGNALS_COLUMNS``).

    Writes the header only when creating the file. Unknown keys are ignored and
    missing columns default to empty, so the log stays schema-stable.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _migrate_schema(path)  # bring an older-schema file up to current columns before appending
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SIGNALS_COLUMNS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in SIGNALS_COLUMNS})


def _migrate_schema(path: Path) -> None:
    """Rewrite an existing ``signals.csv`` under the current ``SIGNALS_COLUMNS`` if its
    header differs (e.g. a schema bump added columns), back-filling new columns blank.

    Additive-safe and idempotent: rows are read by their own header and re-mapped by name,
    so appending never misaligns an older-schema log. Runs only when the header differs.
    """
    if not path.exists() or path.stat().st_size == 0:
        return
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if list(reader.fieldnames or []) == list(SIGNALS_COLUMNS):
            return  # already current — nothing to do
        old_rows = list(reader)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SIGNALS_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in old_rows:
            writer.writerow({col: row.get(col, "") for col in SIGNALS_COLUMNS})


def append_market(row: dict[str, Any], path: Path) -> None:
    """Append one ``market.csv`` row (schema ``MARKET_COLUMNS``) — the run's market context.

    Header written only when creating the file; unknown keys ignored, missing columns blank."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=MARKET_COLUMNS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerow({col: row.get(col, "") for col in MARKET_COLUMNS})


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
