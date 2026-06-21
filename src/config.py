"""Load, validate, and expose ``config.yaml`` as typed objects (SPEC §4.3).

Validation **fails fast** — before any network fetch — so a misconfigured run dies
with a clear message instead of producing a misleading report. This module is also
the *single* place config is merged: ``resolve_wyckoff_params`` overlays a
timeframe's overrides on the defaults, and the warmup history requirement is
*derived* from those resolved params (never a hand-set key) so it can't drift out
of sync when params are tuned during calibration.
"""
from __future__ import annotations

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
    real_move_volume_mult: float  # large range/gap on >= this x median volume = real move, not glitch


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
class MomentumConfig:
    """Momentum strategy params (SPEC §6) — same defaults+per_timeframe shape as Wyckoff."""

    defaults: dict[str, Any]
    per_timeframe: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class SentimentConfig:
    """News-sentiment strategy params (SPEC §6, §12) — same defaults+per_timeframe shape.

    Holds ``source`` (which `NewsSource` to use), ``scorer`` (which `SentimentScorer`), and
    ``lookback_days`` (how far back of headlines to aggregate as-of the bar)."""

    defaults: dict[str, Any]
    per_timeframe: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class ScoringConfig:
    watchlist_threshold: float


@dataclass(frozen=True)
class TradePlanConfig:
    """Trade-planner settings (SPEC §8A.1). Suggested levels + sizing + a management
    playbook — nothing trades. All values are ``[TUNABLE]`` calibration seeds. Flat for
    v1 (per-timeframe overrides deferred — sizing is account-level; YAGNI)."""

    account_notional: float  # $ notional used ONLY to scale the suggested size; nothing trades
    risk_pct: float          # % of notional risked per trade (1% = standard)
    stop_method: str         # how the stop is placed: "capped" | "structural" | "atr" (R:R lever)
    stop_buffer_pct: float   # structural: % beyond the invalidation (spring/upthrust)
    max_stop_pct: float      # capped: stop no further than this % from entry (the R:R cap)
    stop_atr_mult: float     # atr: stop this × ATR from entry
    breakeven_at_r: float    # management: move stop to entry at +this many R
    scale_out_pct: float     # management: take this % off at the measured-move target
    trail_atr_mult: float    # management: trail the remaining runner by this × ATR


#: Valid trade-plan stop methods (kept here so config validation and the planner agree).
STOP_METHODS = ("capped", "structural", "atr")


@dataclass(frozen=True)
class ReviewConfig:
    """Agent-reviewer settings (SPEC §8.5). Off by default; bounded for cost."""

    enabled: bool
    provider: str             # "anthropic" (cloud) | "ollama" (local); env REVIEW_PROVIDER overrides
    model: str
    api_key_env: str
    base_url: str             # ollama endpoint (env OLLAMA_BASE_URL overrides); ignored by anthropic
    max_tokens: int
    max_reviews_per_run: int  # hard cap on LLM calls per run
    only_new: bool            # review only NEW transitions, not still-qualifying ones


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
    momentum: MomentumConfig
    sentiment: SentimentConfig
    scoring: ScoringConfig
    trade_plan: TradePlanConfig
    review: ReviewConfig
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


def _merge_timeframe(
    defaults: dict[str, Any], per_timeframe: dict[str, dict[str, Any]], timeframe: str
) -> dict[str, Any]:
    """``defaults`` overlaid with ``per_timeframe[tf]`` (key by key) — the one merge rule."""
    merged = dict(defaults)
    merged.update(per_timeframe.get(timeframe, {}))
    return merged


def resolve_wyckoff_params(config: Config, timeframe: str) -> dict[str, Any]:
    """Return Wyckoff params for ``timeframe``: ``defaults`` overlaid with
    ``per_timeframe[tf]`` (key by key), plus ``sub_weights``. The single place config
    merging happens, so the strategy reads everything from one bag and never touches
    config structure. ``sub_weights`` is timeframe-independent (rides along unchanged)."""
    params = _merge_timeframe(config.wyckoff.defaults, config.wyckoff.per_timeframe, timeframe)
    params["sub_weights"] = dict(config.wyckoff.sub_weights)
    return params


def resolve_strategy_params(config: Config, name: str, timeframe: str) -> dict[str, Any]:
    """Resolve any enabled strategy's per-timeframe params. Explicit per strategy (like the
    registry) so the scanner can give each strategy its OWN params, not Wyckoff's — the
    plumbing that makes a new strategy "a file + a config block"."""
    if name == "wyckoff":
        return resolve_wyckoff_params(config, timeframe)
    if name == "momentum":
        return _merge_timeframe(config.momentum.defaults, config.momentum.per_timeframe, timeframe)
    if name == "news_sentiment":
        return _merge_timeframe(config.sentiment.defaults, config.sentiment.per_timeframe, timeframe)
    raise ConfigError(f"No params resolver for strategy '{name}'.")


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
    momentum = _require(raw, "momentum", "")
    sentiment = _require(raw, "sentiment", "")
    scoring = _require(raw, "scoring", "")
    trade_plan = _require(raw, "trade_plan", "")
    review = _require(raw, "review", "")
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
            real_move_volume_mult=float(_require(quality, "real_move_volume_mult", "data_quality.")),
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
        momentum=MomentumConfig(
            defaults=dict(_require(momentum, "defaults", "momentum.")),
            per_timeframe={
                tf: dict(overrides or {})
                for tf, overrides in _require(momentum, "per_timeframe", "momentum.").items()
            },
        ),
        sentiment=SentimentConfig(
            defaults=dict(_require(sentiment, "defaults", "sentiment.")),
            per_timeframe={
                tf: dict(overrides or {})
                for tf, overrides in _require(sentiment, "per_timeframe", "sentiment.").items()
            },
        ),
        scoring=ScoringConfig(
            watchlist_threshold=float(_require(scoring, "watchlist_threshold", "scoring.")),
        ),
        trade_plan=TradePlanConfig(
            account_notional=float(_require(trade_plan, "account_notional", "trade_plan.")),
            risk_pct=float(_require(trade_plan, "risk_pct", "trade_plan.")),
            stop_method=str(_require(trade_plan, "stop_method", "trade_plan.")),
            stop_buffer_pct=float(_require(trade_plan, "stop_buffer_pct", "trade_plan.")),
            max_stop_pct=float(_require(trade_plan, "max_stop_pct", "trade_plan.")),
            stop_atr_mult=float(_require(trade_plan, "stop_atr_mult", "trade_plan.")),
            breakeven_at_r=float(_require(trade_plan, "breakeven_at_r", "trade_plan.")),
            scale_out_pct=float(_require(trade_plan, "scale_out_pct", "trade_plan.")),
            trail_atr_mult=float(_require(trade_plan, "trail_atr_mult", "trade_plan.")),
        ),
        review=ReviewConfig(
            enabled=bool(_require(review, "enabled", "review.")),
            provider=str(_require(review, "provider", "review.")),
            model=str(_require(review, "model", "review.")),
            api_key_env=str(_require(review, "api_key_env", "review.")),
            base_url=str(_require(review, "base_url", "review.")),
            max_tokens=int(_require(review, "max_tokens", "review.")),
            max_reviews_per_run=int(_require(review, "max_reviews_per_run", "review.")),
            only_new=bool(_require(review, "only_new", "review.")),
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

    # Momentum needs positive lookbacks; keep them within the fetched history (so the strategy
    # has data) — they're modest vs the Wyckoff-sized warmup, so no extra history is required.
    for name in ("ma_window", "roc_window"):
        if int(config.momentum.defaults.get(name, 0)) <= 0:
            raise ConfigError(f"momentum.defaults.{name} must be a positive integer.")

    # News sentiment needs a positive look-back window; source/scorer are validated lazily
    # (build_news_source / get_scorer fail loud on an unknown name).
    if int(config.sentiment.defaults.get("lookback_days", 0)) <= 0:
        raise ConfigError("sentiment.defaults.lookback_days must be a positive integer.")

    # v1 ships Discord only.
    if config.output.notify.channel != "discord":
        raise ConfigError(
            f"notify.channel must be 'discord' in v1 (got '{config.output.notify.channel}')."
        )

    # Reviewer provider must be a known one. A key being unset is NOT an error (the reviewer
    # just skips, like notify); Ollama needs no key. (REVIEW_PROVIDER can override at runtime.)
    if config.review.enabled and config.review.provider not in ("anthropic", "ollama"):
        raise ConfigError(
            f"review.provider must be 'anthropic' or 'ollama' (got '{config.review.provider}')."
        )

    # NOTE: we intentionally do NOT fail on a referenced env var being set-but-empty.
    # GitHub Actions turns an *unset* secret (e.g. an optional REPORT_BASE_URL) into an
    # empty env var, and the runtime already degrades gracefully (notify is skipped when
    # the webhook is empty; the report link is omitted when the base URL is empty).

    # Trade-planner sizing must be sane (SPEC §8A.1): a non-positive notional or risk %
    # can't scale a position, and a scale-out outside 0-100% is meaningless.
    tp = config.trade_plan
    if tp.account_notional <= 0:
        raise ConfigError(f"trade_plan.account_notional must be > 0 (got {tp.account_notional}).")
    if not (0 < tp.risk_pct <= 100):
        raise ConfigError(f"trade_plan.risk_pct must be in (0, 100] (got {tp.risk_pct}).")
    if tp.stop_buffer_pct < 0:
        raise ConfigError(f"trade_plan.stop_buffer_pct must be >= 0 (got {tp.stop_buffer_pct}).")
    if tp.stop_method not in STOP_METHODS:
        raise ConfigError(f"trade_plan.stop_method must be one of {STOP_METHODS} (got '{tp.stop_method}').")
    if tp.max_stop_pct <= 0:
        raise ConfigError(f"trade_plan.max_stop_pct must be > 0 (got {tp.max_stop_pct}).")
    if tp.stop_atr_mult <= 0:
        raise ConfigError(f"trade_plan.stop_atr_mult must be > 0 (got {tp.stop_atr_mult}).")
    if not (0 <= tp.scale_out_pct <= 100):
        raise ConfigError(f"trade_plan.scale_out_pct must be in [0, 100] (got {tp.scale_out_pct}).")

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
