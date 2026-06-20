"""Tests for config loading, validation, and per-timeframe resolution (SPEC §4.3).

Strategy: use the *shipped* config.yaml as the known-valid baseline, then mutate a
copy to assert each fail-fast rule fires. Testing through the public ``load_config``
(rather than internals) keeps these tests behavior-focused — they won't shatter if
the parsing is refactored.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src import config
from src.config import ConfigError

SHIPPED_CONFIG = Path("config.yaml")


@pytest.fixture
def raw_config() -> dict:
    """The shipped config.yaml parsed to a mutable dict — the valid baseline."""
    with SHIPPED_CONFIG.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def write_config(tmp_path: Path, raw: dict) -> Path:
    """Dump a (possibly mutated) config dict to a temp file and return its path."""
    path = tmp_path / "config.yaml"
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(raw, fh)
    return path


# --- Happy path ---------------------------------------------------------------


def test_shipped_config_is_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    # Don't let a stray empty env var in the test environment trip the env check.
    monkeypatch.delenv("NOTIFY_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("REPORT_BASE_URL", raising=False)
    cfg = config.load_config(SHIPPED_CONFIG)
    assert cfg.timeframes == ["daily", "weekly"]
    assert cfg.output.notify.channel == "discord"


def test_resolve_wyckoff_params_merges_weekly_over_defaults(
    raw_config: dict, tmp_path: Path
) -> None:
    cfg = config.load_config(write_config(tmp_path, raw_config))
    daily = config.resolve_wyckoff_params(cfg, "daily")
    weekly = config.resolve_wyckoff_params(cfg, "weekly")

    # weekly overrides win where present...
    assert weekly["range_lookback"] == 26
    assert weekly["min_range_bars"] == 8
    # ...and inherit defaults everywhere else (incl. trend_lookback: no weekly override).
    assert weekly["high_volume_ratio"] == daily["high_volume_ratio"]
    assert weekly["trend_lookback"] == 60
    # daily uses defaults unchanged.
    assert daily["range_lookback"] == 60
    # sub_weights ride along in the resolved bag (so the strategy needs only params).
    assert weekly["sub_weights"]["volume_behavior"] == 35


# --- scoring_window must include the COMPLETE depth-lookback set ---------------


def test_scoring_window_reflects_every_depth_lookback(
    raw_config: dict, tmp_path: Path
) -> None:
    # climax_window is NOT one of the "big three" today (it's 10). If it's correctly
    # in the depth set, bumping it must move scoring_window — proving the set is
    # complete, not just the three largest-by-default params.
    raw_config["wyckoff"]["defaults"]["climax_window"] = 999
    # defaults apply to both timeframes, so give both warmup checks headroom.
    raw_config["data"]["daily_lookback_days"] = 2000
    raw_config["data"]["weekly_lookback_weeks"] = 2000
    cfg = config.load_config(write_config(tmp_path, raw_config))

    assert config.scoring_window(cfg, "daily") == 999
    assert config.required_history(cfg, "daily") == 999 + 25 + config.WARMUP_MARGIN_BARS


def test_required_history_adds_baseline_and_margin(
    raw_config: dict, tmp_path: Path
) -> None:
    cfg = config.load_config(write_config(tmp_path, raw_config))
    # daily defaults: max depth lookback 60, baseline 25, margin 5 -> 90.
    assert config.required_history(cfg, "daily") == 60 + 25 + config.WARMUP_MARGIN_BARS


# --- Fail-fast validation rules (SPEC §4.3) -----------------------------------


def test_warmup_rejects_too_short_lookback(raw_config: dict, tmp_path: Path) -> None:
    raw_config["data"]["daily_lookback_days"] = 10  # far below required ~90
    with pytest.raises(ConfigError, match="required history"):
        config.load_config(write_config(tmp_path, raw_config))


def test_missing_weekly_per_timeframe_rejected(raw_config: dict, tmp_path: Path) -> None:
    del raw_config["wyckoff"]["per_timeframe"]["weekly"]
    with pytest.raises(ConfigError, match="per_timeframe.weekly"):
        config.load_config(write_config(tmp_path, raw_config))


def test_non_discord_channel_rejected(raw_config: dict, tmp_path: Path) -> None:
    raw_config["output"]["notify"]["channel"] = "telegram"
    with pytest.raises(ConfigError, match="discord"):
        config.load_config(write_config(tmp_path, raw_config))


def test_sub_weights_must_sum_to_100(raw_config: dict, tmp_path: Path) -> None:
    raw_config["wyckoff"]["sub_weights"]["volume_behavior"] = 40  # now sums to 105
    with pytest.raises(ConfigError, match="sum to 100"):
        config.load_config(write_config(tmp_path, raw_config))


def test_empty_referenced_env_var_is_allowed(
    raw_config: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # GitHub Actions turns an unset secret into an empty env var; that must NOT fail
    # validation (the runtime degrades gracefully — notify skipped, report link omitted).
    monkeypatch.setenv("REPORT_BASE_URL", "")
    config.load_config(write_config(tmp_path, raw_config))  # no raise


def test_unset_env_var_is_allowed(
    raw_config: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Unset is fine — the value is supplied at runtime via GitHub Secrets.
    monkeypatch.delenv("NOTIFY_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("REPORT_BASE_URL", raising=False)
    cfg = config.load_config(write_config(tmp_path, raw_config))
    assert cfg.output.notify.webhook_url_env == "NOTIFY_WEBHOOK_URL"


def test_trade_plan_rejects_nonpositive_risk_pct(raw_config: dict, tmp_path: Path) -> None:
    raw_config["trade_plan"]["risk_pct"] = 0  # can't size a position risking 0%
    with pytest.raises(ConfigError, match="risk_pct"):
        config.load_config(write_config(tmp_path, raw_config))


def test_trade_plan_rejects_bad_scale_out(raw_config: dict, tmp_path: Path) -> None:
    raw_config["trade_plan"]["scale_out_pct"] = 150  # a >100% scale-out is meaningless
    with pytest.raises(ConfigError, match="scale_out_pct"):
        config.load_config(write_config(tmp_path, raw_config))


def test_trade_plan_rejects_unknown_stop_method(raw_config: dict, tmp_path: Path) -> None:
    raw_config["trade_plan"]["stop_method"] = "trailing_magic"  # not a known method
    with pytest.raises(ConfigError, match="stop_method"):
        config.load_config(write_config(tmp_path, raw_config))


def test_missing_required_key_rejected(raw_config: dict, tmp_path: Path) -> None:
    del raw_config["timeframes"]
    with pytest.raises(ConfigError, match="timeframes"):
        config.load_config(write_config(tmp_path, raw_config))


def test_missing_config_file_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        config.load_config(tmp_path / "nope.yaml")
