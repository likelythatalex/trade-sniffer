"""Code-diff review CLI — a DeepSeek (cheap tier) pass over a git diff (SPEC §8.5 / reviewers).

Read-only: it runs `git diff <range>`, sends the diff + the repo's conventions through the
``prompts/code_review.md`` rubric, and writes findings to ``review_out/`` (gitignored). It never
edits files. Fail-soft: a missing key / unknown provider / API error omits the review, never
crashes. Run on demand or in CI.

    python -m src.reviewers.code --diff-range HEAD~1..HEAD
"""
from __future__ import annotations

import argparse
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from ..config import Config, load_config
from ..review import Reviewer
from .common import build_tool_reviewer, load_rubric, pinned_docs, truncate_to_tokens, write_report

logger = logging.getLogger(__name__)

# Stable docs pinned into the system prefix (cache-friendly) — the repo's conventions are the
# rubric's reference for "does this fit the codebase?".
_PINNED = ["CLAUDE.md", "docs/strategies.md"]
_DEFAULT_RANGE = "HEAD~1..HEAD"


def run_code_review(
    config: Config, diff_range: str = _DEFAULT_RANGE, reviewer: Reviewer | None = None
) -> Path | None:
    """Review the diff for ``diff_range`` → a markdown report path, or ``None`` (nothing to do /
    fail-soft). ``reviewer`` is injectable for tests; defaults to one built from config + env."""
    diff = _git_diff(diff_range)
    if not diff.strip():
        logger.info("code review: empty diff for %s; nothing to review.", diff_range)
        return None

    tool = config.reviewers.code
    diff, truncated = truncate_to_tokens(diff, tool.max_input_tokens)
    if truncated:
        logger.warning("code review: diff exceeded max_input_tokens (%d); truncated.", tool.max_input_tokens)

    system_prompt = load_rubric("code_review") + "\n\n# Repository conventions (reference)\n\n" + pinned_docs(_PINNED)
    if reviewer is None:
        reviewer = build_tool_reviewer(tool, config.reviewers.deepseek, system_prompt)
    if reviewer is None:
        logger.warning("code review skipped: no reviewer (missing key or unknown provider).")
        return None

    user_prompt = f"Review this git diff (`{diff_range}`):\n\n```diff\n{diff}\n```"
    try:
        result = reviewer.review(user_prompt)
    except Exception:  # fail-soft: an API/network error never crashes the run
        logger.exception("code review failed (fail-soft); no report written.")
        return None

    header = (
        f"# Code review — `{diff_range}`\n\n"
        f"_Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} · "
        f"model {tool.model}{' · diff truncated' if truncated else ''}. "
        f"Analyst notes for human review — not authoritative._\n\n---\n\n"
    )
    return write_report("code_review", header + result["text"] + "\n")


def _git_diff(diff_range: str) -> str:
    """``git diff <range>`` (read-only). Returns ``""`` on any git error (fail-soft)."""
    try:
        completed = subprocess.run(
            ["git", "diff", diff_range], capture_output=True, text=True, check=False
        )
    except (OSError, ValueError):
        logger.exception("git diff failed")
        return ""
    if completed.returncode != 0:
        logger.warning("git diff %s exited %d: %s", diff_range, completed.returncode, completed.stderr.strip())
        return ""
    return completed.stdout


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="DeepSeek code-diff review (read-only → review_out/).")
    parser.add_argument("--diff-range", default=_DEFAULT_RANGE, help="git diff range (default HEAD~1..HEAD)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    path = run_code_review(load_config(), args.diff_range)
    if path is not None:
        logger.info("code review written: %s", path)


if __name__ == "__main__":
    main()
