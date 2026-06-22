"""Tests for the standalone review CLIs (src/reviewers/*). Hermetic — a StubReviewer stands
in for the LLM and review_out is redirected to tmp, so nothing hits the network or the repo.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

from src import config as config_module
from src.review import Reviewer
from src.reviewers import code as code_review
from src.reviewers import common
from src.reviewers import outcome as outcome_review

CONFIG = config_module.load_config(Path("config.yaml"))


class StubReviewer(Reviewer):
    def __init__(self, text: str = "Summary: looks fine.\nBlocking: none.") -> None:
        self.text = text
        self.calls = 0
        self.last_prompt: str | None = None

    def review(self, prompt: str) -> dict[str, str]:
        self.calls += 1
        self.last_prompt = prompt
        return {"text": self.text, "verdict": "n/a"}


# --- config wiring ------------------------------------------------------------


def test_reviewers_config_parsed() -> None:
    rc = CONFIG.reviewers
    assert rc.deepseek.api_key_env == "DEEPSEEK_API_KEY"
    assert rc.code.model == "deepseek-v4-flash" and rc.outcome.model == "deepseek-v4-pro"
    assert rc.code.provider == "deepseek" and rc.outcome.max_input_tokens > 0


# --- common helpers -----------------------------------------------------------


def test_truncate_to_tokens() -> None:
    long, truncated = common.truncate_to_tokens("x" * 100, max_input_tokens=10)  # budget 40 chars
    assert truncated and "TRUNCATED" in long and len(long) < 100 + 60
    short, untouched = common.truncate_to_tokens("short", max_input_tokens=10)
    assert not untouched and short == "short"


def test_build_tool_reviewer_no_key_is_none(monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("REVIEW_PROVIDER", raising=False)
    assert common.build_tool_reviewer(CONFIG.reviewers.code, CONFIG.reviewers.deepseek, "sys") is None


# --- code review --------------------------------------------------------------


def test_run_code_review_writes_report(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(common, "REVIEW_OUT", tmp_path / "review_out")
    monkeypatch.setattr(code_review, "_git_diff", lambda _range: "diff --git a/x.py b/x.py\n+changed")
    stub = StubReviewer()

    path = code_review.run_code_review(CONFIG, "HEAD~1..HEAD", reviewer=stub)

    assert path is not None and path.parent == tmp_path / "review_out"
    assert path.name.startswith("code_review_")
    text = path.read_text(encoding="utf-8")
    assert "looks fine" in text and "HEAD~1..HEAD" in text
    assert "```diff" in stub.last_prompt and "+changed" in stub.last_prompt  # diff sent to the model


def test_run_code_review_empty_diff_returns_none(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(common, "REVIEW_OUT", tmp_path / "out")
    monkeypatch.setattr(code_review, "_git_diff", lambda _range: "   \n")
    stub = StubReviewer()
    assert code_review.run_code_review(CONFIG, reviewer=stub) is None
    assert stub.calls == 0  # nothing to review -> no LLM call


def test_run_code_review_no_reviewer_is_fail_soft(tmp_path, monkeypatch) -> None:
    # No key -> build_tool_reviewer returns None -> run returns None, writes nothing.
    monkeypatch.setattr(common, "REVIEW_OUT", tmp_path / "out")
    monkeypatch.setattr(code_review, "_git_diff", lambda _range: "diff --git a/x b/x\n+y")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("REVIEW_PROVIDER", raising=False)
    assert code_review.run_code_review(CONFIG) is None
    assert not (tmp_path / "out").exists()


# --- outcome review -----------------------------------------------------------


def _config_with_output(tmp_path: Path) -> config_module.Config:
    out = dataclasses.replace(CONFIG.output, dir=str(tmp_path))
    return dataclasses.replace(CONFIG, output=out)


def test_gather_data_packs_present_files(tmp_path) -> None:
    (tmp_path / "signals.csv").write_text("run_ts,ticker,composite_score\n2026-01-01,AAA,72\n", encoding="utf-8")
    (tmp_path / "market.csv").write_text("run_ts,regime\n2026-01-01,risk-on\n", encoding="utf-8")
    data = outcome_review._gather_data(tmp_path, journal_path=tmp_path / "absent.csv", results_dir=tmp_path / "none")
    assert "signals.csv" in data and "AAA" in data and "market.csv" in data and "risk-on" in data


def test_run_outcome_review_writes_report(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(common, "REVIEW_OUT", tmp_path / "review_out")
    monkeypatch.setattr(outcome_review, "_gather_data", lambda _dir: "## signals.csv\n\nAAA,72\n")
    stub = StubReviewer(text="Summary: too little data yet.")

    path = outcome_review.run_outcome_review(_config_with_output(tmp_path), reviewer=stub)

    assert path is not None and path.name.startswith("outcome_review_")
    assert "too little data" in path.read_text(encoding="utf-8")
    assert "signals.csv" in stub.last_prompt and "AAA" in stub.last_prompt  # data packed into the prompt


def test_run_outcome_review_no_data_returns_none(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(common, "REVIEW_OUT", tmp_path / "out")
    monkeypatch.setattr(outcome_review, "_gather_data", lambda _dir: "")
    stub = StubReviewer()
    assert outcome_review.run_outcome_review(_config_with_output(tmp_path), reviewer=stub) is None
    assert stub.calls == 0  # no data -> no LLM call
