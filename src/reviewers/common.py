"""Shared plumbing for the standalone review CLIs — rubric loading, payload truncation,
provider construction, and report writing. Pure-ish + fail-soft; no trade logic here.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from ..config import DeepSeekClientConfig, ReviewerToolConfig
from ..review import Reviewer, build_provider

logger = logging.getLogger(__name__)

# All standalone-review output is confined here (gitignored). Reviewers NEVER write elsewhere.
REVIEW_OUT = Path("review_out")
_PROMPTS_DIR = Path("prompts")
# Rough chars-per-token for truncation. We don't ship a tokenizer (no-SDK ethos); this is a
# deliberately conservative proxy so we under-fill rather than overflow the model's context.
_CHARS_PER_TOKEN = 4
_TRUNCATION_MARK = "\n\n[...TRUNCATED — payload exceeded max_input_tokens...]\n"


def load_rubric(name: str) -> str:
    """Read a version-controlled persona rubric from ``prompts/<name>.md``."""
    return (_PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def pinned_docs(paths: list[str]) -> str:
    """Concatenate stable repo docs (skipping any missing) for the system-prompt prefix.

    Kept in the *system* prompt (fixed prefix) so prefix-caching providers like DeepSeek charge
    the cache-hit rate on repeat runs. Returns ``""`` if none are present."""
    blocks: list[str] = []
    for path in paths:
        file = Path(path)
        if file.exists():
            blocks.append(f"<doc path=\"{path}\">\n{file.read_text(encoding='utf-8')}\n</doc>")
        else:
            logger.info("pinned doc not found, skipping: %s", path)
    return "\n\n".join(blocks)


def truncate_to_tokens(text: str, max_input_tokens: int) -> tuple[str, bool]:
    """Cap ``text`` to ~``max_input_tokens`` (char proxy). Returns ``(text, was_truncated)``;
    callers log the notice. Truncates the *tail* — the diff/data head is usually most relevant."""
    budget = max_input_tokens * _CHARS_PER_TOKEN
    if len(text) <= budget:
        return text, False
    return text[:budget] + _TRUNCATION_MARK, True


def build_tool_reviewer(
    tool: ReviewerToolConfig, deepseek: DeepSeekClientConfig, system_prompt: str
) -> Reviewer | None:
    """Construct a reviewer for one tool from its config + the shared DeepSeek client block,
    honoring the same ``REVIEW_PROVIDER``/``REVIEW_MODEL``/``REVIEW_BASE_URL`` env overrides as
    the in-pipeline reviewer. Returns ``None`` (caller skips, fail-soft) when no key / unknown
    provider. ``verdicts=()`` — these reports have no aligned/mixed/skeptical verdict."""
    import os

    provider = os.environ.get("REVIEW_PROVIDER", tool.provider)
    model = os.environ.get("REVIEW_MODEL", tool.model)
    base_url = os.environ.get("REVIEW_BASE_URL") or deepseek.base_url
    return build_provider(
        provider, model, deepseek.api_key_env, base_url, tool.max_tokens,
        system_prompt=system_prompt, verdicts=(), timeout=tool.timeout, retries=tool.retries,
    )


def write_report(name: str, text: str) -> Path:
    """Write ``review_out/<name>_<timestamp>.md`` (the ONLY place a reviewer writes)."""
    REVIEW_OUT.mkdir(parents=True, exist_ok=True)
    path = REVIEW_OUT / f"{name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    path.write_text(text, encoding="utf-8")
    return path
