"""Private trade journal — record the trades you WOULD take, then reflect on them (SPEC §8A.2).

PRIVATE BY DESIGN. The repo and gh-pages are public, so this never touches either: the
journal file (`journal.csv` by default) is gitignored, written only on your machine, run only
manually — never in CI, never published. It closes the loop the dashboard starts:
``signal → plan → journal → outcome → reflect``.

Step 4 is the **data layer only** — add / list / close trades. Auto-computed outcomes
(realized R, which level hit first) and the private post-trade agent review come next
(§8A.2, ROADMAP steps 5-6). The store is a small CSV rewritten on each mutation (KISS:
the file is tiny and edited by one person), tolerant of a missing/empty file.

This tool **never places a trade** — it records intended ones for review.
"""
from __future__ import annotations

import argparse
import csv
import logging
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from .review import Reviewer, load_reviews, save_reviews
from .trade_outcome import TradeOutcome, evaluate_outcome

logger = logging.getLogger(__name__)

DEFAULT_JOURNAL_PATH = Path("journal.csv")  # repo root, gitignored (never committed/published)
TRADE_REVIEWS_PATH = Path("trade_reviews.json")  # gitignored cache of post-trade reflections
DEFAULT_HTML_PATH = Path("journal_report.html")  # gitignored private view (Phase A)
_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
_JOURNAL_TEMPLATE = "journal.html.j2"

# The post-trade reflection rubric — distinct from the signal reviewer's (review.SYSTEM_PROMPT).
# It judges PROCESS separately from OUTCOME (a good process can lose; a bad one can win).
POST_TRADE_VERDICTS = ("good", "mixed", "poor")
POST_TRADE_SYSTEM_PROMPT = (
    "You are a trading-journal reflection assistant. Given one CLOSED trade — its plan, the "
    "recorded exit, and how price actually behaved — write a concise, objective post-mortem for "
    "the trader's private journal. Separate PROCESS (was the setup sound and the plan followed?) "
    "from OUTCOME (a single win/loss can be luck): a good process can lose and a bad one can win "
    "— judge the process. Note what went well, what didn't, and one concrete repeatable lesson.\n\n"
    "IMPORTANT: Do NOT give advice about future trades or what to buy/sell — these are reflective "
    "notes on a PAST trade.\n\n"
    "Output format:\n"
    "Process: <good|mixed|poor>   (setup + execution quality, independent of P/L)\n"
    "<2-4 sentence reflection>\n"
    "Lessons:\n"
    "- <1 to 3 short bullets>"
)

# Trade-centric directions (the journal is about trades, not signals). The signal terms
# accumulation/distribution map onto these for convenience when copying from the dashboard.
_DIRECTION_ALIASES = {"accumulation": "long", "distribution": "short", "long": "long", "short": "short"}

JOURNAL_COLUMNS = (
    "id", "opened_date", "ticker", "timeframe", "direction",
    "entry", "stop", "target", "size", "source",
    "status", "exit_date", "exit_price", "notes",
)


class JournalError(Exception):
    """Raised on an invalid journal operation (bad direction, unknown id, bad number)."""


@dataclass
class TradeEntry:
    """One intended (never-executed) trade. ``id`` is a stable sequential handle for ``close``."""

    id: int
    opened_date: str
    ticker: str
    direction: str   # "long" | "short"
    entry: float
    stop: float
    target: float
    size: float
    timeframe: str = ""
    source: str = ""
    status: str = "open"  # "open" | "closed"
    exit_date: str = ""
    exit_price: float | str = ""  # "" while open
    notes: str = ""


# --- store I/O (tolerant, rewrite-on-mutate) ----------------------------------


def load_entries(path: Path = DEFAULT_JOURNAL_PATH) -> list[dict[str, Any]]:
    """Load all journal rows; a missing/empty file is an empty journal (never crash)."""
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_entries(path: Path, entries: list[dict[str, Any]]) -> None:
    """Rewrite the whole journal (the file is small and single-user)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=JOURNAL_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for entry in entries:
            writer.writerow({col: entry.get(col, "") for col in JOURNAL_COLUMNS})


# --- operations ---------------------------------------------------------------


def add_trade(
    path: Path,
    *,
    ticker: str,
    direction: str,
    entry: float,
    stop: float,
    target: float,
    size: float,
    timeframe: str = "",
    source: str = "",
    notes: str = "",
    today: date | None = None,
) -> dict[str, Any]:
    """Append an open trade and return its row. Validates direction + positive numbers; the
    id is the next integer after the current max (stable handle for ``close``)."""
    norm_dir = _DIRECTION_ALIASES.get(direction.lower())
    if norm_dir is None:
        raise JournalError(f"direction must be one of {sorted(set(_DIRECTION_ALIASES))} (got '{direction}').")
    for name, value in (("entry", entry), ("stop", stop), ("target", target), ("size", size)):
        if value <= 0:
            raise JournalError(f"{name} must be > 0 (got {value}).")

    entries = load_entries(path)
    next_id = max((int(e["id"]) for e in entries), default=0) + 1
    record = TradeEntry(
        id=next_id,
        opened_date=(today or date.today()).isoformat(),
        ticker=ticker.upper(),
        direction=norm_dir,
        entry=float(entry), stop=float(stop), target=float(target), size=float(size),
        timeframe=timeframe, source=source, notes=notes,
    )
    row = asdict(record)
    entries.append(row)
    write_entries(path, entries)
    return row


def close_trade(
    path: Path,
    trade_id: int,
    *,
    exit_price: float,
    exit_date: date | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Mark a trade closed with its exit; raise if the id is unknown or already closed."""
    if exit_price <= 0:
        raise JournalError(f"exit_price must be > 0 (got {exit_price}).")
    entries = load_entries(path)
    for entry in entries:
        if int(entry["id"]) == trade_id:
            if entry.get("status") == "closed":
                raise JournalError(f"trade {trade_id} is already closed.")
            entry["status"] = "closed"
            entry["exit_price"] = float(exit_price)
            entry["exit_date"] = (exit_date or date.today()).isoformat()
            if notes is not None:
                entry["notes"] = notes
            write_entries(path, entries)
            return entry
    raise JournalError(f"no trade with id {trade_id}.")


def list_trades(path: Path, status: str | None = None) -> list[dict[str, Any]]:
    """All trades, or only those matching ``status`` ("open"/"closed")."""
    entries = load_entries(path)
    return [e for e in entries if status is None or e.get("status") == status]


# --- outcomes (pure; prices passed in) ----------------------------------------


def evaluate_entries(
    entries: list[dict[str, Any]], prices: dict[str, pd.DataFrame]
) -> list[tuple[dict[str, Any], TradeOutcome | None]]:
    """Pair each entry with its path-dependent outcome, given ticker→OHLC price frames.

    Pure (prices injected, no I/O) so it's unit-testable. Outcomes are *derived*, never
    stored in journal.csv — the journal stays pure user input (no schema churn). The forward
    path is bars strictly after the trade's opened date, so the entry bar can't self-resolve.
    A ticker with no price data, or a too-recent trade, yields ``None`` (no outcome yet).
    """
    results: list[tuple[dict[str, Any], TradeOutcome | None]] = []
    for entry in entries:
        df = prices.get(entry["ticker"])
        outcome = None
        if df is not None:
            forward = _bars_after(df, entry["opened_date"])
            outcome = evaluate_outcome(
                entry["direction"], float(entry["entry"]), float(entry["stop"]),
                float(entry["target"]), forward,
            )
        results.append((entry, outcome))
    return results


def _bars_after(df: pd.DataFrame, opened_date: str) -> pd.DataFrame:
    """Forward bars strictly after ``opened_date`` (entry bar excluded)."""
    return df[df.index > pd.Timestamp(opened_date)]


# --- post-trade reflection (PRIVATE; reuses the Reviewer interface) ------------


def review_closed_trades(
    entries_with_outcomes: list[tuple[dict[str, Any], TradeOutcome | None]],
    reviewer: Reviewer,
    cache: dict[str, Any],
    max_reviews: int,
) -> dict[str, Any]:
    """Generate a private post-trade reflection for each CLOSED, not-yet-reviewed trade,
    capped at ``max_reviews`` LLM calls, and return the updated cache (keyed by trade id).

    Reviewer is injected (stub in tests). Fail-soft: a failed review is skipped, never fatal.
    Closed-only because reflection needs a finished trade; cached so re-runs never re-spend.
    """
    pending = [
        (entry, outcome)
        for entry, outcome in entries_with_outcomes
        if entry.get("status") == "closed" and str(entry["id"]) not in cache
    ]
    for entry, outcome in pending[:max_reviews]:
        try:
            result = reviewer.review(build_trade_review_prompt(entry, outcome))
        except Exception:  # fail soft: a review failure never breaks the command
            logger.exception("post-trade review failed for trade %s", entry["id"])
            continue
        cache[str(entry["id"])] = {**result, "ticker": entry["ticker"]}
    return cache


def build_trade_review_prompt(entry: dict[str, Any], outcome: TradeOutcome | None) -> str:
    """Compact, structured evidence for one closed trade (kept small to bound tokens)."""
    risk = abs(float(entry["entry"]) - float(entry["stop"]))
    planned_rr = abs(float(entry["target"]) - float(entry["entry"])) / risk if risk > 0 else float("nan")
    lines = [
        f"Trade: {entry['ticker']} {entry['direction']}, opened {entry['opened_date']}"
        + (f", source {entry['source']}" if entry.get("source") else ""),
        f"Plan: entry {entry['entry']}, stop {entry['stop']}, target {entry['target']} "
        f"(planned R:R {planned_rr:.2f})",
    ]
    if entry.get("exit_price"):
        actual = _actual_realized_r(entry)
        lines.append(
            f"Recorded exit: {entry['exit_price']} on {entry.get('exit_date', '?')}"
            + (f" (actual {actual:+.2f}R)" if actual is not None else "")
        )
    if outcome is not None:
        plan_r = "n/a" if outcome.realized_r is None else f"{outcome.realized_r:+.2f}R"
        lines.append(
            f"Price path: {outcome.resolution} after {outcome.bars_held} bars; "
            f"plan-realized {plan_r}; MFE {outcome.mfe_r:.2f}R; MAE {outcome.mae_r:.2f}R"
        )
    else:
        lines.append("Price path: no data available")
    if entry.get("notes"):
        lines.append(f"Trader notes: {entry['notes']}")
    lines.append("\nReflect on this closed trade per your rubric.")
    return "\n".join(lines)


def _actual_realized_r(entry: dict[str, Any]) -> float | None:
    """Realized R from the trader's RECORDED exit (vs the plan-simulated outcome)."""
    risk = abs(float(entry["entry"]) - float(entry["stop"]))
    if risk <= 0 or not entry.get("exit_price"):
        return None
    exit_price = float(entry["exit_price"])
    move = (exit_price - float(entry["entry"])) if entry["direction"] == "long" else (float(entry["entry"]) - exit_price)
    return move / risk


# --- private HTML view (Phase A; read-only, local-only) ------------------------


def render_journal_html(
    entries_with_outcomes: list[tuple[dict[str, Any], TradeOutcome | None]],
    reviews: dict[str, Any],
    out_path: Path = DEFAULT_HTML_PATH,
) -> Path:
    """Render the private journal page (trades + outcomes + reflections + summary) to a
    gitignored HTML file. Pure-ish: data in, file out (no fetch) — easy to test. Autoescape
    is on, so trader notes + LLM text are escaped. PRIVATE: never published."""
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    rows = _journal_view(entries_with_outcomes, reviews)
    environment = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )
    html = environment.get_template(_JOURNAL_TEMPLATE).render(
        rows=rows, summary=_journal_summary(rows), generated=date.today().isoformat()
    )
    out_path = Path(out_path)
    out_path.write_text(html, encoding="utf-8")
    return out_path


def _journal_view(
    entries_with_outcomes: list[tuple[dict[str, Any], TradeOutcome | None]], reviews: dict[str, Any]
) -> list[dict[str, Any]]:
    """Flatten each (entry, outcome) + its cached reflection into a template row."""
    rows: list[dict[str, Any]] = []
    for entry, outcome in entries_with_outcomes:
        actual = _actual_realized_r(entry)
        rows.append({
            "id": entry["id"], "ticker": entry["ticker"], "direction": entry["direction"],
            "opened_date": entry["opened_date"], "status": entry.get("status", "open"),
            "entry": entry["entry"], "stop": entry["stop"], "target": entry["target"], "size": entry["size"],
            "source": entry.get("source", ""), "notes": entry.get("notes", ""),
            "exit_price": entry.get("exit_price", ""), "exit_date": entry.get("exit_date", ""),
            "actual_r": None if actual is None else round(actual, 2),
            "outcome": None if outcome is None else {
                "resolution": outcome.resolution, "realized_r": outcome.realized_r,
                "mfe_r": round(outcome.mfe_r, 2), "mae_r": round(outcome.mae_r, 2),
                "bars_held": outcome.bars_held,
            },
            "review": reviews.get(str(entry["id"])),
        })
    return rows


def _journal_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate stats over CLOSED trades' actual realized R (the real result, not the plan)."""
    closed = [r for r in rows if r["status"] == "closed" and r["actual_r"] is not None]
    n = len(closed)
    wins = sum(1 for r in closed if r["actual_r"] > 0)
    total_r = sum(r["actual_r"] for r in closed)
    return {
        "total": len(rows),
        "open": sum(1 for r in rows if r["status"] == "open"),
        "closed": n,
        "wins": wins,
        "losses": n - wins,
        "win_rate": round(100 * wins / n, 1) if n else None,
        "total_r": round(total_r, 2) if n else None,
        "avg_r": round(total_r / n, 2) if n else None,
    }


# --- CLI (local, manual) ------------------------------------------------------


def _format_row(e: dict[str, Any]) -> str:
    base = f"#{e['id']} {e['ticker']} {e['direction']} entry {e['entry']} stop {e['stop']} target {e['target']} size {e['size']} [{e['status']}]"
    if e.get("status") == "closed":
        base += f" exit {e['exit_price']} on {e['exit_date']}"
    return base


def _format_outcome(e: dict[str, Any], outcome: TradeOutcome | None) -> str:
    head = f"#{e['id']} {e['ticker']} {e['direction']}"
    if outcome is None:
        return f"{head}: no price data / too recent — no outcome yet"
    r = "n/a" if outcome.realized_r is None else f"{outcome.realized_r:+.2f}R"
    return (
        f"{head}: {outcome.resolution} after {outcome.bars_held} bars · realized {r} · "
        f"MFE {outcome.mfe_r:.2f}R · MAE {outcome.mae_r:.2f}R"
    )


def main(argv: list[str] | None = None) -> None:
    """Local CLI: add / list / close trades in the private journal."""
    parser = argparse.ArgumentParser(description="Private trade journal (local-only; never trades).")
    parser.add_argument("--file", default=str(DEFAULT_JOURNAL_PATH), help="journal CSV path (gitignored)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="record an intended trade")
    p_add.add_argument("ticker")
    p_add.add_argument("direction", help="long|short (or accumulation|distribution)")
    p_add.add_argument("entry", type=float)
    p_add.add_argument("stop", type=float)
    p_add.add_argument("target", type=float)
    p_add.add_argument("size", type=float, help="position size in shares")
    p_add.add_argument("--timeframe", default="")
    p_add.add_argument("--source", default="", help="e.g. 'wyckoff daily 2026-06-20'")
    p_add.add_argument("--notes", default="")

    p_list = sub.add_parser("list", help="list trades")
    p_list.add_argument("--status", choices=["open", "closed"], help="filter by status")

    p_close = sub.add_parser("close", help="close a trade by id")
    p_close.add_argument("id", type=int)
    p_close.add_argument("exit_price", type=float)
    p_close.add_argument("--exit-date", default=None, help="YYYY-MM-DD (default: today)")
    p_close.add_argument("--notes", default=None)

    sub.add_parser("report", help="evaluate each trade's outcome vs price history (fetches data)")
    sub.add_parser("review", help="private post-trade reflection on closed trades (needs API key)")
    sub.add_parser("html", help="render the private journal view to a local HTML file")

    args = parser.parse_args(argv)
    path = Path(args.file)

    try:
        if args.command == "add":
            row = add_trade(
                path, ticker=args.ticker, direction=args.direction, entry=args.entry,
                stop=args.stop, target=args.target, size=args.size,
                timeframe=args.timeframe, source=args.source, notes=args.notes,
            )
            print("added", _format_row(row))
        elif args.command == "list":
            rows = list_trades(path, args.status)
            if not rows:
                print("(no trades)")
            for row in rows:
                print(_format_row(row))
        elif args.command == "close":
            exit_date = date.fromisoformat(args.exit_date) if args.exit_date else None
            row = close_trade(path, args.id, exit_price=args.exit_price, exit_date=exit_date, notes=args.notes)
            print("closed", _format_row(row))
        elif args.command == "report":
            _run_report(path)
        elif args.command == "review":
            _run_review(path)
        elif args.command == "html":
            _run_html(path)
    except JournalError as exc:
        parser.error(str(exc))


def _run_report(path: Path) -> None:
    """Fetch daily price history for the journal's tickers and print each trade's outcome.
    Daily bars are used for ALL trades (finer stop/target resolution than weekly). Local
    import keeps add/list/close free of the config/data (yfinance) dependency."""
    from .config import load_config
    from .data import fetch_many

    entries = load_entries(path)
    if not entries:
        print("(no trades)")
        return
    tickers = sorted({e["ticker"] for e in entries})
    fetched = fetch_many(tickers, "daily", load_config())
    prices = {ticker: result.df for ticker, result in fetched.items()}
    for entry, outcome in evaluate_entries(entries, prices):
        print(_format_outcome(entry, outcome))


def _run_review(path: Path) -> None:
    """Private post-trade reflection on CLOSED trades: fetch prices, evaluate outcomes, ask the
    LLM for a process-vs-outcome post-mortem (capped + cached), and print. Local-only; the
    reviews cache (`trade_reviews.json`) is gitignored. Explicit command, so it only needs the
    API key (no review.enabled gate)."""
    from .config import load_config
    from .data import fetch_many
    from .review import build_reviewer

    config = load_config()
    # Build by provider (Anthropic or local Ollama) with env overrides — explicit command, so
    # no enabled gate. Locally set REVIEW_PROVIDER=ollama for a free, fully-private review.
    reviewer = build_reviewer(
        config.review, system_prompt=POST_TRADE_SYSTEM_PROMPT, verdicts=POST_TRADE_VERDICTS
    )
    if reviewer is None:
        print(f"no reviewer available (set {config.review.api_key_env}, or REVIEW_PROVIDER=ollama)")
        return
    entries = load_entries(path)
    if not any(e.get("status") == "closed" for e in entries):
        print("(no closed trades to review)")
        return

    tickers = sorted({e["ticker"] for e in entries})
    fetched = fetch_many(tickers, "daily", config)
    prices = {ticker: result.df for ticker, result in fetched.items()}
    paired = evaluate_entries(entries, prices)
    cache = load_reviews(TRADE_REVIEWS_PATH)
    cache = review_closed_trades(paired, reviewer, cache, config.review.max_reviews_per_run)
    save_reviews(TRADE_REVIEWS_PATH, cache)

    for entry, _ in paired:
        if entry.get("status") != "closed":
            continue
        review = cache.get(str(entry["id"]))
        if review:
            print(f"#{entry['id']} {entry['ticker']} [{review.get('verdict', 'n/a')}]\n{review.get('text', '')}\n")


def _run_html(path: Path) -> None:
    """Render the private journal page. Fetches daily prices best-effort for outcomes (degrades
    to blank outcomes if offline), loads cached reflections, writes the gitignored HTML."""
    entries = load_entries(path)
    prices: dict[str, pd.DataFrame] = {}
    try:
        from .config import load_config
        from .data import fetch_many

        tickers = sorted({e["ticker"] for e in entries})
        if tickers:
            fetched = fetch_many(tickers, "daily", load_config())
            prices = {ticker: result.df for ticker, result in fetched.items()}
    except Exception:  # best-effort: a fetch failure just leaves outcomes blank, page still renders
        logger.warning("price fetch failed; journal page will show blank outcomes")

    paired = evaluate_entries(entries, prices)
    reviews = load_reviews(TRADE_REVIEWS_PATH)
    out = render_journal_html(paired, reviews, DEFAULT_HTML_PATH)
    print(f"wrote {out}  (PRIVATE — local only, gitignored)")


if __name__ == "__main__":
    main()
