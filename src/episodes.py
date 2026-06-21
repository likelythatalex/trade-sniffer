"""Episode (transition history) reconstruction from the signals.csv log.

An **episode** is a maximal run of *consecutive* scans, for one ticker on one timeframe,
where the ticker qualified for the watchlist (``made_watchlist`` true). A setup that flags,
continues a few runs, then invalidates is one episode; if it later re-qualifies, that's a
second episode. This lets us tell a *re-flagged* setup ("seen this before, it failed") from a
genuinely first-time one — context the agent reviewer and the dashboard both want.

Reconstructed from the append-only ``signals.csv`` (the historical record) rather than a new
persisted structure: one source of truth, no schema change, and the same reconstruction the
future failed→revived study will use (ROADMAP). Pure — callers read the rows and pass them in
(I/O stays in the scanner / ``report.read_signals``).
"""
from __future__ import annotations

from dataclasses import dataclass

_TRUTHY = {"true", "1", "yes"}


@dataclass(frozen=True)
class Episode:
    """One qualifying span for a ticker/timeframe.

    Attributes:
        direction: the setup's direction at the start of the episode.
        start_ts: ``run_ts`` of the first qualifying run in the span.
        end_ts: ``run_ts`` of the last qualifying run in the span.
        n_runs: how many consecutive scans the setup qualified for.
        peak_score: the highest composite score reached during the episode.
        ongoing: True if the episode reaches the most recent run in the log (i.e. it hadn't
            invalidated as of the latest scan represented in ``rows``).
    """

    direction: str
    start_ts: str
    end_ts: str
    n_runs: int
    peak_score: float
    ongoing: bool


def reconstruct_episodes(rows: list[dict], ticker: str, timeframe: str) -> list[Episode]:
    """All episodes for ``ticker`` on ``timeframe``, oldest first, from ``signals.csv`` rows.

    The run *timeline* is the sorted set of distinct ``run_ts`` for the timeframe (ISO strings
    sort chronologically); two qualifying runs belong to the same episode only if they're
    adjacent in that timeline. A run where the ticker was present but non-qualifying — or absent
    entirely (e.g. a no-data skip) — breaks the span, starting a new episode on the next flag.
    """
    tf_rows = [r for r in rows if r.get("timeframe") == timeframe and r.get("run_ts")]
    timeline = sorted({r["run_ts"] for r in tf_rows})
    if not timeline:
        return []
    position = {ts: i for i, ts in enumerate(timeline)}
    latest = len(timeline) - 1

    qualifying = {
        r["run_ts"]: r
        for r in tf_rows
        if r.get("ticker") == ticker and _truthy(r.get("made_watchlist"))
    }
    if not qualifying:
        return []

    episodes: list[Episode] = []
    span: list[dict] = []
    for ts in sorted(qualifying, key=lambda t: position[t]):
        if span and position[ts] != position[span[-1]["run_ts"]] + 1:
            episodes.append(_episode_from_span(span, position, latest))
            span = []
        span.append(qualifying[ts])
    if span:
        episodes.append(_episode_from_span(span, position, latest))
    return episodes


def prior_episodes(episodes: list[Episode]) -> list[Episode]:
    """The episodes that already ended (exclude the current/ongoing one). When reconstructed
    from the log *before* the current run is appended, these are the genuinely prior flags."""
    return [e for e in episodes if not e.ongoing]


def format_episode_history(episodes: list[Episode]) -> str | None:
    """A one-line summary of prior episodes for the reviewer prompt + dashboard, or ``None``
    when there are none (a first-time flag needs no history line)."""
    prior = prior_episodes(episodes)
    if not prior:
        return None
    last = prior[-1]
    times = "once" if len(prior) == 1 else f"{len(prior)} times"
    return (
        f"Flagged {times} before on this timeframe. Most recent prior episode: "
        f"{last.direction}, peaked {last.peak_score:.0f}/100 over "
        f"{last.n_runs} run(s), then invalidated (last seen {last.end_ts[:10]})."
    )


# --- helpers ------------------------------------------------------------------


def _episode_from_span(span: list[dict], position: dict[str, int], latest: int) -> Episode:
    return Episode(
        direction=span[0].get("direction", ""),
        start_ts=span[0]["run_ts"],
        end_ts=span[-1]["run_ts"],
        n_runs=len(span),
        peak_score=max(_to_float(r.get("composite_score")) for r in span),
        ongoing=position[span[-1]["run_ts"]] == latest,
    )


def _truthy(value: object) -> bool:
    return str(value).strip().lower() in _TRUTHY


def _to_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
