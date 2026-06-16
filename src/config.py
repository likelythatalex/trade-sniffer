"""Load, validate, and expose ``config.yaml`` as typed objects (SPEC §4.3).

Validation **fails fast** — before any network fetch — so a misconfigured run dies
with a clear message instead of producing a misleading report. This module is also
the *single* place config is merged: ``resolve_wyckoff_params`` overlays a
timeframe's overrides on the defaults, and the warmup history requirement is
*derived* from those resolved params (never a hand-set key) so it can't drift out
of sync when params are tuned during calibration.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path("config.yaml")

# Safety buffer (bars) added on top of scoring_window + baseline_window. Absorbs
# off-by-one effects and the within-window look-forward params (spring_snapback_bars,
# climax reaction) so the earliest *scored* bar always has a full baseline. Cheap.
WARMUP_MARGIN_BARS = 5

# The resolved per-timeframe Wyckoff params that reach back *before* the evaluated
# bar. scoring_window = max of these, so the warmup check tracks the deepest one
# automatically and can never silently break when any param is tuned larger.
#
# Deliberately EXCLUDED (do not add without re-reading this):
#   - min_range_bars: a minimum *count inside* range_lookback, not extra depth.
#   - spring_snapback_bars, climax_reaction_atr: look *forward* from the signal bar
#     to the evaluated bar (within the window), not backward before it.
#   - all threshold params (*_ratio, *_atr, *_pctile, spring_wick_pct,
#     range_extreme_fraction): not time windows at all.
_DEPTH_LOOKBACK_PARAMS = (
    "range_lookback",
    "trend_lookback",
    "spring_lookback",
    "climax_window",
    "no_demand_supply_median_window",
)

_VALID_TIMEFRAMES = ("daily", "weekly")


class ConfigError(Exception):
    """Raised when config.yaml is missing or semantically invalid (fail fast)."""


# --- Typed view over config.yaml (no raw dict access downstream, SPEC §4.3) ----
# Sections are typed dataclasses; the inherently open-ended bags (Wyckoff params,
# per-timeframe overrides, sub-weights, baseline windows) stay dicts on purpose —
# typing every tunable leaf would be churn for no safety gain.


@dataclass(frozen=True)
class DataConfig:
    source: str
    daily_lookback_days: int
    weekly_lookback_weeks: int
    weekly_fetch: str
    cache_dir: str


@dataclass(frozen=True)
class SymbolsConfig:
    exchange_source: str
    override_map_file: str
    skip_if_unresolved: bool


@dataclass(frozen=True)
class FeaturesConfig:
    baseline_window: dict[str, int]  # per timeframe


@dataclass(frozen=True)
class DataQualityConfig:
    max_bar_range_atr_mult: float
    min_valid_bars_pct: float
    drop_zero_volume_bars: bool
    verify_split_adjustment: bool


@dataclass(frozen=True)
class LiquidityConfig:
    min_avg_dollar_volume: float
    min_price: float


@dataclass(frozen=True)
class StrategySpec:
    enabled: bool
    weight: float


@dataclass(frozen=True)
class WyckoffConfig:
    defaults: dict[str, Any]
    per_timeframe: dict[str, dict[str, Any]]
    sub_weights: dict[str, float]


@dataclass(frozen=True)
class ScoringConfig:
    watchlist_threshold: float


@dataclass(frozen=True)
class NotifyConfig:
    enabled: bool
    channel: str
    webhook_url_env: str
    report_base_url_env: str
    suppress_empty: bool


@dataclass(frozen=True)
class OutputConfig:
    dir: str
    report_title: str
    embed_chart_interval: dict[str, str]
    theme: str
    write_tv_import_file: bool
    notify: NotifyConfig


@dataclass(frozen=True)
class Config:
    timeframes: list[str]
    universe_file: str
    data: DataConfig
    symbols: SymbolsConfig
    features: FeaturesConfig
    data_quality: DataQualityConfig
    liquidity: LiquidityConfig
    strategies: dict[str, StrategySpec]
    wyckoff: WyckoffConfig
    scoring: ScoringConfig
    output: OutputConfig


# --- Public API ---------------------------------------------------------------


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    """Parse and validate ``config.yaml``; raise ``ConfigError`` on the first problem."""
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict):
        raise ConfigError(f"Config file {path} did not parse to a mapping.")

    config = _build_config(raw)
    _validate(config)
    return config


def resolve_wyckoff_params(config: Config, timeframe: str) -> dict[str, Any]:
    """Return Wyckoff params for ``timeframe``: ``defaults`` overlaid with
    ``per_timeframe[tf]`` (key by key). The single place config merging happens."""
    params = dict(config.wyckoff.defaults)
    params.update(config.wyckoff.per_timeframe.get(timeframe, {}))
    return params


def scoring_window(config: Config, timeframe: str) -> int:
    """Deepest historical lookback (bars) the strategy needs for ``timeframe`` —
    the max over the resolved depth-lookback params (see ``_DEPTH_LOOKBACK_PARAMS``)."""
    params = resolve_wyckoff_params(config, timeframe)
    return max(int(params[name]) for name in _DEPTH_LOOKBACK_PARAMS)


def required_history(config: Config, timeframe: str) -> int:
    """Minimum bars to fetch so the earliest scored bar has a full baseline:
    ``scoring_window + baseline_window + margin`` (SPEC §5.1 warmup)."""
    baseline = int(config.features.baseline_window[timeframe])
    return scoring_window(config, timeframe) + baseline + WARMUP_MARGIN_BARS


# --- Building & validation ----------------------------------------------------


def _require(mapping: Any, key: str, ctx: str) -> Any:
    """Fetch ``mapping[key]`` or raise a ConfigError naming the full path."""
    if not isinstance(mapping, dict) or key not in mapping:
        raise ConfigError(f"Missing required config key: {ctx}{key}")
    return mapping[key]


def _build_config(raw: dict) -> Config:
    """Map the parsed YAML into typed dataclasses, failing on any missing key."""
    data = _require(raw, "data", "")
    symbols = _require(raw, "symbols", "")
    features = _require(raw, "features", "")
    quality = _require(raw, "data_quality", "")
    liquidity = _require(raw, "liquidity", "")
    strategies = _require(raw, "strategies", "")
    wyckoff = _require(raw, "wyckoff", "")
    scoring = _require(raw, "scoring", "")
    output = _require(raw, "output", "")
    notify = _require(output, "notify", "output.")

    return Config(
        timeframes=list(_require(raw, "timeframes", "")),
        universe_file=str(_require(raw, "universe_file", "")),
        data=DataConfig(
            source=str(_require(data, "source", "data.")),
            daily_lookback_days=int(_require(data, "daily_lookback_days", "data.")),
            weekly_lookback_weeks=int(_require(data, "weekly_lookback_weeks", "data.")),
            weekly_fetch=str(_require(data, "weekly_fetch", "data.")),
            cache_dir=str(_require(data, "cache_dir", "data.")),
        ),
        symbols=SymbolsConfig(
            exchange_source=str(_require(symbols, "exchange_source", "symbols.")),
            override_map_file=str(_require(symbols, "override_map_file", "symbols.")),
            skip_if_unresolved=bool(_require(symbols, "skip_if_unresolved", "symbols.")),
        ),
        features=FeaturesConfig(
            baseline_window=dict(_require(features, "baseline_window", "features.")),
        ),
        data_quality=DataQualityConfig(
            max_bar_range_atr_mult=float(_require(quality, "max_bar_range_atr_mult", "data_quality.")),
            min_valid_bars_pct=float(_require(quality, "min_valid_bars_pct", "data_quality.")),
            drop_zero_volume_bars=bool(_require(quality, "drop_zero_volume_bars", "data_quality.")),
            verify_split_adjustment=bool(_require(quality, "verify_split_adjustment", "data_quality.")),
        ),
        liquidity=LiquidityConfig(
            min_avg_dollar_volume=float(_require(liquidity, "min_avg_dollar_volume", "liquidity.")),
            min_price=float(_require(liquidity, "min_price", "liquidity.")),
        ),
        strategies={
            name: StrategySpec(
                enabled=bool(_require(spec, "enabled", f"strategies.{name}.")),
                weight=float(_require(spec, "weight", f"strategies.{name}.")),
            )
            for name, spec in strategies.items()
        },
        wyckoff=WyckoffConfig(
            defaults=dict(_require(wyckoff, "defaults", "wyckoff.")),
            per_timeframe={
                tf: dict(overrides or {})
                for tf, overrides in _require(wyckoff, "per_timeframe", "wyckoff.").items()
            },
            sub_weights=dict(_require(wyckoff, "sub_weights", "wyckoff.")),
        ),
        scoring=ScoringConfig(
            watchlist_threshold=float(_require(scoring, "watchlist_threshold", "scoring.")),
        ),
        output=OutputConfig(
            dir=str(_require(output, "dir", "output.")),
            report_title=str(_require(output, "report_title", "output.")),
            embed_chart_interval=dict(_require(output, "embed_chart_interval", "output.")),
            theme=str(_require(output, "theme", "output.")),
            write_tv_import_file=bool(_require(output, "write_tv_import_file", "output.")),
            notify=NotifyConfig(
                enabled=bool(_require(notify, "enabled", "output.notify.")),
                channel=str(_require(notify, "channel", "output.notify.")),
                webhook_url_env=str(_require(notify, "webhook_url_env", "output.notify.")),
                report_base_url_env=str(_require(notify, "report_base_url_env", "output.notify.")),
                suppress_empty=bool(_require(notify, "suppress_empty", "output.notify.")),
            ),
        ),
    )


def _validate(config: Config) -> None:
    """Apply the fail-fast semantic rules (SPEC §4.3)."""
    if not config.timeframes:
        raise ConfigError("timeframes must list at least one timeframe.")
    for tf in config.timeframes:
        if tf not in _VALID_TIMEFRAMES:
            raise ConfigError(f"Unsupported timeframe '{tf}' (v1 supports {_VALID_TIMEFRAMES}).")

    # Per-timeframe Wyckoff overrides are required for weekly (a 60-week range is not
    # a 60-day range) — SPEC D6.
    if "weekly" in config.timeframes and "weekly" not in config.wyckoff.per_timeframe:
        raise ConfigError(
            "weekly is enabled but wyckoff.per_timeframe.weekly is missing."
        )

    # Every depth-lookback param must exist in defaults so scoring_window can resolve.
    for name in _DEPTH_LOOKBACK_PARAMS:
        if name not in config.wyckoff.defaults:
            raise ConfigError(f"wyckoff.defaults is missing required param '{name}'.")

    # v1 ships Discord only.
    if config.output.notify.channel != "discord":
        raise ConfigError(
            f"notify.channel must be 'discord' in v1 (got '{config.output.notify.channel}')."
        )

    # A referenced env var that is *set but empty* is a mistake; unset is fine (the
    # value arrives at runtime via secrets).
    for env_name in (config.output.notify.webhook_url_env, config.output.notify.report_base_url_env):
        if env_name and env_name in os.environ and os.environ[env_name].strip() == "":
            raise ConfigError(f"Env var '{env_name}' is referenced but set but empty.")

    # Wyckoff sub-score weights must sum to 100 (they form a 0-100 composite).
    total = sum(config.wyckoff.sub_weights.values())
    if abs(total - 100) > 1e-9:
        raise ConfigError(f"wyckoff.sub_weights must sum to 100 (got {total}).")

    # Warmup: each timeframe's configured lookback must cover the derived requirement.
    for tf in config.timeframes:
        if tf not in config.features.baseline_window:
            raise ConfigError(f"features.baseline_window.{tf} is missing.")
        need = required_history(config, tf)
        have = config.data.daily_lookback_days if tf == "daily" else config.data.weekly_lookback_weeks
        if have < need:
            raise ConfigError(
                f"{tf} lookback {have} < required history {need} "
                f"(scoring_window {scoring_window(config, tf)} + baseline "
                f"{config.features.baseline_window[tf]} + margin {WARMUP_MARGIN_BARS})."
            )
