"""Outcome review CLI — a DeepSeek (stronger tier) pass over the accrued logs (reviewers).

Analysis-only: it gathers the scanner's outputs (signals.csv, market.csv, the optional private
journal.csv, and any offline backtest/event-study reports), sends them + the methodology through
the ``prompts/outcome_review.md`` rubric, and writes a read of *system* performance to
``review_out/`` (gitignored). It never edits files and **never gives trading advice**. Fail-soft.

    python -m src.reviewers.outcome
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path

from ..config import Config, load_config
from ..review import Reviewer
from .common import build_tool_reviewer, load_rubric, pinned_docs, truncate_to_tokens, write_report

logger = logging.getLogger(__name__)

_PINNED = ["docs/appendix.md", "docs/wyckoff_methodology.md"]
_SIGNALS_TAIL = 800  # recent signal rows are what matter; the head is bounded by truncation anyway


def run_outcome_review(config: Config, reviewer: Reviewer | None = None) -> Path | None:
    """Review the accrued logs → a markdown report path, or ``None`` (no data / fail-soft).
    ``reviewer`` is injectable for tests; defaults to one built from config + env."""
    data = _gather_data(Path(config.output.dir))
    if not data.strip():
        logger.info("outcome review: no data found (no signals.csv yet); nothing to review.")
        return None

    tool = config.reviewers.outcome
    data, truncated = truncate_to_tokens(data, tool.max_input_tokens)
    if truncated:
        logger.warning("outcome review: data exceeded max_input_tokens (%d); truncated.", tool.max_input_tokens)

    system_prompt = load_rubric("outcome_review") + "\n\n# Methodology (reference)\n\n" + pinned_docs(_PINNED)
    if reviewer is None:
        reviewer = build_tool_reviewer(tool, config.reviewers.deepseek, system_prompt)
    if reviewer is None:
        logger.warning("outcome review skipped: no reviewer (missing key or unknown provider).")
        return None

    try:
        result = reviewer.review("Review the system's accrued outputs below:\n\n" + data)
    except Exception:  # fail-soft
        logger.exception("outcome review failed (fail-soft); no report written.")
        return None

    header = (
        f"# Outcome review\n\n_Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} · "
        f"model {tool.model}{' · data truncated' if truncated else ''}. "
        f"Analysis of the system for human review — NOT trading advice._\n\n---\n\n"
    )
    return write_report("outcome_review", header + result["text"] + "\n")


def _gather_data(
    output_dir: Path,
    journal_path: Path = Path("journal.csv"),
    results_dir: Path = Path("backtest_results"),
) -> str:
    """Assemble a bounded data pack from the accrued outputs (missing files are skipped).

    signals.csv is tailed (recent rows matter); market.csv + journal.csv are small enough to
    include whole; backtest_results/ contributes its rendered ``.md`` summaries (not raw CSVs).
    ``journal_path``/``results_dir`` are params so the gather is unit-testable in isolation."""
    blocks: list[str] = []
    blocks.append(_csv_block("signals.csv", output_dir / "signals.csv", tail=_SIGNALS_TAIL))
    blocks.append(_csv_block("market.csv", output_dir / "market.csv", tail=None))
    blocks.append(_csv_block("journal.csv", journal_path, tail=None))  # private, repo-root, gitignored

    if results_dir.exists():
        for md in sorted(results_dir.glob("*.md")):
            blocks.append(f"## backtest_results/{md.name}\n\n{md.read_text(encoding='utf-8')}")

    return "\n\n".join(b for b in blocks if b)


def _csv_block(label: str, path: Path, tail: int | None) -> str:
    """A labelled CSV block: full, or the header + last ``tail`` rows with a count note. ``""``
    if the file is absent (fail-soft — these inputs are all optional)."""
    if not path.exists() or path.stat().st_size == 0:
        return ""
    lines = path.read_text(encoding="utf-8").splitlines()
    if tail is not None and len(lines) > tail + 1:
        shown = [lines[0]] + lines[-tail:]
        note = f" (showing last {tail} of {len(lines) - 1} rows)"
    else:
        shown, note = lines, ""
    return f"## {label}{note}\n\n```\n" + "\n".join(shown) + "\n```"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="DeepSeek outcome review over accrued logs (analysis-only → review_out/).")
    parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    path = run_outcome_review(load_config())
    if path is not None:
        logger.info("outcome review written: %s", path)


if __name__ == "__main__":
    main()
