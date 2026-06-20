"""Wyckoff accumulation/distribution strategy — the only strategy in v1 (SPEC §6A).

Detects the structural fingerprints of accumulation/distribution — trading range,
volume behavior (effort vs. result), spring/upthrust — over the RELATIVE features
from ``features.py``, and emits a directional 0-100 conviction score.

Model (first-pass; the score mapping is a CALIBRATION SEED, not final — SPEC §6.4):
each sub-score is a *signed* value in [-100, +100] (+ = accumulation, - = distribution).
The composite is their weighted average via ``sub_weights``; its sign is the direction
and its magnitude the conviction. Within a sub-score, signals start at EQUAL weight
(methodology §5) — i.e. the mean of the firing signals.

A valid trading range is a PRECONDITION: Wyckoff accumulation/distribution happens in
a range, so a chart without one scores 0 / ``none`` rather than flagging on trend
context alone. Signals whose features are NaN simply don't fire (abstain), so the
composite is always finite (NaN never propagates — SPEC §6.4).
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from .base import Levels, Strategy, StrategyContext, StrategyResult

# Calibration seeds (could move to config later; kept here as the first-pass defaults).
DIRECTION_FLOOR = 10.0  # |composite| below this -> direction "none"
TREND_FULL_SCALE = 0.20  # net move over trend_lookback that maps to full trend conviction
RS_FULL_SCALE = 0.10  # [TUNABLE] out/under-performance vs SPY over the lookback for full RS
CONTRACTION_FULL_SCALE = 0.5  # [TUNABLE] recent vol this fraction below earlier vol = full coil
SPRING_BASE_FRACTION = 0.5  # [TUNABLE] a detected spring/upthrust scores at least this much;
#                              wick rejection + volume corroboration fill the rest to full.
ATR_WINDOW = 14  # [TUNABLE] standard ATR lookback; the volatility measure the planner stop uses.

_SUB_SCORES = ("range_structure", "volume_behavior", "spring_upthrust", "confirmation")


class WyckoffStrategy(Strategy):
    """Range + volume + spring analysis → directional conviction score."""

    name = "wyckoff"

    def evaluate(self, df: pd.DataFrame, context: StrategyContext) -> StrategyResult:
        params = context.params
        features = context.features
        range_info = detect_trading_range(df, features, params)

        atr = _recent_atr(df, ATR_WINDOW)

        # Precondition: no valid range -> not a Wyckoff setup. Score 0, direction none.
        if not range_info["valid"]:
            return StrategyResult(
                direction="none",
                score=0.0,
                sub_scores={name: 0.0 for name in _SUB_SCORES},
                reasons=["no valid trading range"],
                metadata={"range": range_info},
                levels=Levels(range_high=range_info["range_high"], range_low=range_info["range_low"], atr=atr),
            )

        volume_score, volume_reasons, climax = score_volume_behavior(df, features, range_info, params)
        spring = detect_spring_upthrust(df, features, range_info, params)
        confirmation_score, confirmation_reasons, confirmation_breakdown = score_confirmation(
            df, features, range_info, params, context.prior_state, context.benchmark_close
        )

        sub_scores = {
            "range_structure": _range_structure_score(range_info, params),
            "volume_behavior": volume_score,
            "spring_upthrust": spring["score"],
            "confirmation": confirmation_score,
        }

        weights = params["sub_weights"]
        # sub_weights sum to 100 (validated in config), so this is a weighted average.
        composite_signed = sum(sub_scores[k] * weights[k] for k in _SUB_SCORES) / sum(weights.values())

        if composite_signed >= DIRECTION_FLOOR:
            direction = "accumulation"
        elif composite_signed <= -DIRECTION_FLOOR:
            direction = "distribution"
        else:
            direction = "none"

        reasons = volume_reasons + spring["reasons"] + confirmation_reasons
        levels = Levels(
            range_high=range_info["range_high"],
            range_low=range_info["range_low"],
            spring_low=spring["spring_low"],        # None unless a spring was detected
            upthrust_high=spring["upthrust_high"],  # None unless an upthrust was detected
            atr=atr,
        )
        return StrategyResult(
            direction=direction,
            score=abs(composite_signed),
            sub_scores=sub_scores,
            reasons=reasons,
            metadata={
                "range": range_info,
                "composite_signed": composite_signed,
                "is_spring": spring["is_spring"],
                "is_upthrust": spring["is_upthrust"],
                "spring_bar": spring["bar"],  # timestamp of the spring/upthrust bar, for chart marker
                # Confirmed Selling/Buying Climax bar (Event #2), for the chart marker. None if absent.
                "climax_bar": climax["bar"] if climax else None,
                "climax_type": climax["type"] if climax else None,
                "confirmation": confirmation_breakdown,  # per-input contributions, for logging
            },
            levels=levels,
        )


# --- Pure sub-steps (SPEC §6.1-6.4). Each is independently unit-testable. ---


def detect_trading_range(
    df: pd.DataFrame, features: pd.DataFrame, params: dict[str, Any]
) -> dict[str, Any]:
    """§6.1 — the support/resistance band over the last ``range_lookback`` bars.

    Returns the band, its width (% of mid-price), bar count, validity (tight + long
    enough), and where the last close sits within it. ``position_in_range`` (0 = at
    support, 1 = at resistance) is the single definition of "near support/resistance".
    """
    n = min(int(params["range_lookback"]), len(df))
    window = df.iloc[-n:]
    range_high = float(window["high"].max())
    range_low = float(window["low"].min())
    width = range_high - range_low
    mid = (range_high + range_low) / 2.0
    width_pct = (width / mid * 100.0) if mid > 0 else float("inf")

    valid = (
        n >= int(params["min_range_bars"])
        and width_pct <= float(params["range_max_width_pct"])
        and width > 0
    )

    last_close = float(df["close"].iloc[-1])
    position = (last_close - range_low) / width if width > 0 else float("nan")
    fraction = float(params["range_extreme_fraction"])
    near_support = position <= fraction if pd.notna(position) else False
    near_resistance = position >= (1.0 - fraction) if pd.notna(position) else False

    return {
        "range_high": range_high,
        "range_low": range_low,
        "width": width,
        "width_pct": width_pct,
        "n_bars": n,
        "valid": valid,
        "position_in_range": position,
        "near_support": near_support,
        "near_resistance": near_resistance,
    }


def _range_structure_score(range_info: dict[str, Any], params: dict[str, Any]) -> float:
    """range_structure sub-score: directional bias from where price sits in a valid
    range. Ramps from 0 at the third-boundary to ±100 at the extreme (+ near support)."""
    position = range_info["position_in_range"]
    if pd.isna(position):
        return 0.0
    fraction = float(params["range_extreme_fraction"])
    if range_info["near_support"]:
        proximity = (fraction - position) / fraction  # 1 at support, 0 at boundary
        return 100.0 * _clip01(proximity)
    if range_info["near_resistance"]:
        proximity = (position - (1.0 - fraction)) / fraction
        return -100.0 * _clip01(proximity)
    return 0.0


def score_volume_behavior(
    df: pd.DataFrame, features: pd.DataFrame, range_info: dict[str, Any], params: dict[str, Any]
) -> tuple[float, list[str], dict[str, Any] | None]:
    """§6.2 — effort vs. result, No Demand/No Supply, climax. Signed; mean of the
    firing signals (equal-weight seed). + = accumulation, - = distribution.

    Returns ``(score, reasons, climax)`` where ``climax`` is the confirmed Selling/Buying
    Climax bar (or ``None``), surfaced so it can be marked on the dashboard chart."""
    contributions: list[float] = []
    reasons: list[str] = []

    volume_ratio = _last(features, "volume_ratio")
    spread_atr = _last(features, "spread_atr")
    near_support = range_info["near_support"]
    near_resistance = range_info["near_resistance"]
    high_volume = float(params["high_volume_ratio"])
    narrow = float(params["narrow_spread_atr"])

    last_close = float(df["close"].iloc[-1])
    last_open = float(df["open"].iloc[-1])
    is_up_bar = last_close > last_open
    is_down_bar = last_close < last_open

    # Effort vs. result: high volume, narrow spread = absorption/exhaustion at an extreme.
    if pd.notna(volume_ratio) and pd.notna(spread_atr) and volume_ratio >= high_volume and spread_atr <= narrow:
        if near_support:
            contributions.append(100.0)
            reasons.append("absorption at support (high volume, narrow spread)")
        elif near_resistance:
            contributions.append(-100.0)
            reasons.append("supply at resistance (high volume, narrow spread)")

    # No Supply: narrow down-bar on below-median volume at support (bullish).
    if near_support and is_down_bar and pd.notna(volume_ratio) and volume_ratio < 1.0 and pd.notna(spread_atr) and spread_atr <= narrow:
        contributions.append(100.0)
        reasons.append("no supply (narrow down-bar, low volume at support)")
    # No Demand: narrow up-bar on below-median volume at resistance (bearish).
    if near_resistance and is_up_bar and pd.notna(volume_ratio) and volume_ratio < 1.0 and pd.notna(spread_atr) and spread_atr <= narrow:
        contributions.append(-100.0)
        reasons.append("no demand (narrow up-bar, low volume at resistance)")

    # Climax: a volume spike at an extreme that is FOLLOWED BY a sharp reaction (the
    # exhaustion-then-reversal that actually defines a climax — a spike with no reaction
    # is just a spike). See _score_climax.
    climax_contribution, climax_reason, climax = _score_climax(df, features, range_info, params)
    if climax_contribution is not None:
        contributions.append(climax_contribution)
        reasons.append(climax_reason)

    score = sum(contributions) / len(contributions) if contributions else 0.0
    return score, reasons, climax


def _score_climax(
    df: pd.DataFrame, features: pd.DataFrame, range_info: dict[str, Any], params: dict[str, Any]
) -> tuple[float | None, str | None, dict[str, Any] | None]:
    """Climax at a range extreme — the book's Event #2 (Selling Climax / Buying Climax):
    a volume spike (``volume_ratio`` ≥ ``high_volume_ratio``) *and* a subsequent sharp
    reaction of at least ``climax_reaction_atr`` × ATR away from the climax bar's extreme.
    The reaction is the confirmation the prior code lacked — a bare spike now abstains.

    Returns ``(contribution, reason, climax)`` where ``climax`` is ``{"bar", "type"}`` for a
    confirmed climax (so it can be marked on the chart) or ``None``. All three are ``None``
    when mid-range, no spike, or no reaction yet (climax too recent to judge).
    ``volume_pctile`` alternative stays deferred."""
    near_support = range_info.get("near_support", False)
    near_resistance = range_info.get("near_resistance", False)
    if not (near_support or near_resistance):
        return None, None, None

    n = len(df)
    window = min(int(params["climax_window"]), n)
    high_volume = float(params["high_volume_ratio"])
    vol = features["volume_ratio"].iloc[-window:]
    peak = vol.max()
    if pd.isna(peak) or peak < high_volume:
        return None, None, None  # no genuine volume spike in the window

    climax_idx = n - window + int(vol.to_numpy().argmax())
    after = df.iloc[climax_idx + 1 :]
    if after.empty:
        return None, None, None  # climax is the last bar -> reaction not observable yet

    atr = float((df["high"].iloc[-window:] - df["low"].iloc[-window:]).mean())
    if atr <= 0:
        return None, None, None
    threshold = float(params["climax_reaction_atr"]) * atr
    climax_bar = df.index[climax_idx]

    if near_support:  # selling climax -> price should rally off the climax low
        reaction = float(after["close"].max()) - float(df["low"].iloc[climax_idx])
        if reaction >= threshold:
            return 100.0, "selling-climax + reaction near support", {"bar": climax_bar, "type": "selling_climax"}
    if near_resistance:  # buying climax -> price should drop off the climax high
        reaction = float(df["high"].iloc[climax_idx]) - float(after["close"].min())
        if reaction >= threshold:
            return -100.0, "buying-climax + reaction near resistance", {"bar": climax_bar, "type": "buying_climax"}
    return None, None, None


def detect_spring_upthrust(
    df: pd.DataFrame, features: pd.DataFrame, range_info: dict[str, Any], params: dict[str, Any]
) -> dict[str, Any]:
    """§6.3 — spring (false break below support) / upthrust (false break above
    resistance), confirmed by a snapback close back inside the range.

    Terminology note: what we call "upthrust" is, in the book's vocabulary, the
    **UTAD** (Upthrust After Distribution) — the *Phase-C* shakeout that breaks the
    Phase A/B highs and is the tradeable mirror of a Spring. The book reserves bare
    "Upthrust/UT" for a *minor Phase-B* test of the AR high, which we do not model.

    Detection (break + snapback) is the GATE; given it, the magnitude scales from a
    base floor up to full with two quality confirmations — a rejection wick on the
    false-break bar (``spring_wick_pct``) and volume corroboration — so a textbook
    shakeout outscores a marginal poke. (Quality weights are equal-weight seeds.)
    """
    n = len(df)
    spring_lookback = int(params["spring_lookback"])
    snapback = int(params["spring_snapback_bars"])
    range_lookback = min(int(params["range_lookback"]), n)
    wick_threshold = float(params["spring_wick_pct"]) / 100.0

    result = {
        "score": 0.0, "is_spring": False, "is_upthrust": False, "reasons": [], "bar": None,
        "spring_low": None, "upthrust_high": None,  # the false-break extreme price (for Levels)
    }

    established_end = n - spring_lookback
    established_start = n - range_lookback
    if established_end - established_start < 2:  # need a prior band to break
        return result

    established_support = float(df["low"].iloc[established_start:established_end].min())
    established_resistance = float(df["high"].iloc[established_start:established_end].max())
    recent = df.iloc[established_end:]
    recent_lows = list(recent["low"])
    recent_highs = list(recent["high"])
    recent_closes = list(recent["close"])
    last_close = float(df["close"].iloc[-1])

    # Spring: a recent bar breaks below support, a close snaps back inside within
    # snapback bars, and we are back inside now.
    breaks_down = [i for i, low in enumerate(recent_lows) if low < established_support]
    if breaks_down and last_close >= established_support:
        first = breaks_down[0]
        if any(c >= established_support for c in recent_closes[first : first + snapback + 1]):
            spring_idx = established_end + min(breaks_down, key=lambda i: recent_lows[i])  # deepest poke
            quality, q_reasons = _false_break_quality(df, features, spring_idx, established_end, "spring", wick_threshold)
            result["is_spring"] = True
            result["bar"] = df.index[spring_idx]
            result["spring_low"] = float(df["low"].iloc[spring_idx])  # invalidation reference
            result["score"] = 100.0 * _quality_to_strength(quality)
            result["reasons"] = ["spring: false break below support, recovered inside"] + q_reasons

    # Upthrust: mirror image at resistance.
    breaks_up = [i for i, high in enumerate(recent_highs) if high > established_resistance]
    if breaks_up and last_close <= established_resistance:
        first = breaks_up[0]
        if any(c <= established_resistance for c in recent_closes[first : first + snapback + 1]):
            up_idx = established_end + max(breaks_up, key=lambda i: recent_highs[i])  # highest poke
            quality, q_reasons = _false_break_quality(df, features, up_idx, established_end, "upthrust", wick_threshold)
            result["is_upthrust"] = True
            result["bar"] = df.index[up_idx]
            result["upthrust_high"] = float(df["high"].iloc[up_idx])  # invalidation reference
            result["score"] = -100.0 * _quality_to_strength(quality)
            result["reasons"] = ["upthrust: false break above resistance, rejected"] + q_reasons

    if result["is_spring"] and result["is_upthrust"]:  # ambiguous -> abstain
        result["score"] = 0.0
        result["reasons"] = []
        result["bar"] = None
        result["spring_low"] = None
        result["upthrust_high"] = None
    return result


def _quality_to_strength(quality: float) -> float:
    """Map a [0,1] confirmation quality to a [BASE,1] strength: a detected false break
    always counts (the base floor), confirmations fill the rest."""
    return SPRING_BASE_FRACTION + (1.0 - SPRING_BASE_FRACTION) * quality


def _false_break_quality(
    df: pd.DataFrame,
    features: pd.DataFrame,
    bar_idx: int,
    recent_start: int,
    kind: str,
    wick_threshold: float,
) -> tuple[float, list[str]]:
    """Quality of a false-break bar in [0,1] = equal-weight mean of two confirmations:
    a rejection wick (closed back toward the range, ``spring_wick_pct``) and volume
    corroboration (above-median volume on the bar). Returns ``(quality, reasons)``."""
    high = float(df["high"].iloc[bar_idx])
    low = float(df["low"].iloc[bar_idx])
    close = float(df["close"].iloc[bar_idx])
    bar_range = high - low
    reasons: list[str] = []

    # Rejection wick: for a spring, closing near the high rejects the lows; mirror for upthrust.
    if bar_range > 0:
        rejection = (close - low) / bar_range if kind == "spring" else (high - close) / bar_range
    else:
        rejection = 0.0
    wick_ok = rejection >= wick_threshold
    if wick_ok:
        reasons.append("on a rejection wick")

    # Volume corroboration: the shakeout/poke bar trades above its rolling-median volume.
    bar_volume_ratio = features["volume_ratio"].iloc[bar_idx]
    volume_ok = bool(pd.notna(bar_volume_ratio) and bar_volume_ratio > 1.0)
    if volume_ok:
        reasons.append("with volume corroboration")

    quality = (float(wick_ok) + float(volume_ok)) / 2.0
    return quality, reasons


def score_confirmation(
    df: pd.DataFrame,
    features: pd.DataFrame,
    range_info: dict[str, Any],
    params: dict[str, Any],
    mtf_direction: str | None = None,
    benchmark_close: pd.Series | None = None,
) -> tuple[float, list[str], dict[str, float | None]]:
    """§7 confirmation (signed; mean of the firing inputs). Implements TREND CONTEXT,
    RS-vs-SPY, and MTF agreement. Returns ``(score, reasons, breakdown)`` where
    ``breakdown`` holds each input's signed contribution (or ``None`` if it abstained),
    so the scanner can log them to signals.csv. Volatility contraction still abstains
    (next Tier-2 item)."""
    contributions: list[float] = []
    reasons: list[str] = []
    breakdown: dict[str, float | None] = {"trend": None, "rs": None, "vol_contraction": None, "mtf": None}

    n = len(df)
    lookback = min(int(params["trend_lookback"]), n - 1) if n >= 2 else 0

    # Trend context: a prior downtrend (negative net move) favors accumulation.
    if n >= 2:
        prior_close = float(df["close"].iloc[-1 - lookback])
        last_close = float(df["close"].iloc[-1])
        if prior_close > 0:
            pct_change = (last_close - prior_close) / prior_close
            trend = _clip(-pct_change / TREND_FULL_SCALE, -1.0, 1.0) * 100.0
            contributions.append(trend)
            breakdown["trend"] = trend
            if trend >= DIRECTION_FLOOR:
                reasons.append("prior downtrend (accumulation context)")
            elif trend <= -DIRECTION_FLOOR:
                reasons.append("prior uptrend (distribution context)")

    # RS vs SPY (§7.1): out-performing the benchmark over the lookback is a bullish tell.
    if benchmark_close is not None and n >= 2:
        rs = _relative_strength(df, benchmark_close, lookback)
        if rs is not None:
            contributions.append(rs)
            breakdown["rs"] = rs
            if rs >= DIRECTION_FLOOR:
                reasons.append("outperforming SPY (relative strength)")
            elif rs <= -DIRECTION_FLOOR:
                reasons.append("underperforming SPY (relative weakness)")

    # Volatility contraction (§7.2): a coil tightening near an extreme precedes the move.
    vol_contraction = score_vol_contraction(df, range_info, params)
    if vol_contraction is not None:
        contributions.append(vol_contraction)
        breakdown["vol_contraction"] = vol_contraction
        if vol_contraction >= DIRECTION_FLOOR:
            reasons.append("volatility contraction near support (coil)")
        elif vol_contraction <= -DIRECTION_FLOOR:
            reasons.append("volatility contraction near resistance (coil)")

    # MTF agreement: the other timeframe's stored direction is directional evidence now.
    if mtf_direction == "accumulation":
        contributions.append(100.0)
        breakdown["mtf"] = 100.0
        reasons.append("MTF: other timeframe in accumulation")
    elif mtf_direction == "distribution":
        contributions.append(-100.0)
        breakdown["mtf"] = -100.0
        reasons.append("MTF: other timeframe in distribution")

    score = sum(contributions) / len(contributions) if contributions else 0.0
    return score, reasons, breakdown


def _relative_strength(
    df: pd.DataFrame, benchmark_close: pd.Series, lookback: int
) -> float | None:
    """Signed RS contribution: the stock's return minus the benchmark's over the same
    ``lookback`` bars, scaled to [-100, +100] (+ = out-performance → accumulation bias).
    The benchmark is aligned onto the stock's index (forward-filling minor gaps).
    Returns ``None`` (abstain) when it can't be computed on a degenerate/short series."""
    bench = benchmark_close.reindex(df.index).ffill()
    if len(bench) <= lookback:
        return None
    bench_now = bench.iloc[-1]
    bench_then = bench.iloc[-1 - lookback]
    stock_now = float(df["close"].iloc[-1])
    stock_then = float(df["close"].iloc[-1 - lookback])
    if not (pd.notna(bench_now) and pd.notna(bench_then)) or bench_then <= 0 or stock_then <= 0:
        return None
    stock_return = (stock_now - stock_then) / stock_then
    bench_return = (float(bench_now) - float(bench_then)) / float(bench_then)
    return _clip((stock_return - bench_return) / RS_FULL_SCALE, -1.0, 1.0) * 100.0


def score_vol_contraction(
    df: pd.DataFrame, range_info: dict[str, Any], params: dict[str, Any]
) -> float | None:
    """§7.2 volatility contraction ("the coil"): recent bar ranges tightening vs earlier
    in the trading range often precedes the expansion move. It's directionless on its own,
    so direction comes from range location — reusing the single near-support/resistance
    definition: a coil near support is a bullish (accumulation) coil, near resistance a
    bearish one. Returns a signed contribution, or ``None`` (abstain) when mid-range, when
    volatility isn't contracting, or when it can't be measured."""
    near_support = range_info.get("near_support", False)
    near_resistance = range_info.get("near_resistance", False)
    if not (near_support or near_resistance):
        return None  # no directional read mid-range
    range_n = min(int(params["range_lookback"]), len(df))
    recent_n = min(int(params["vol_contraction_window"]), range_n)
    if range_n - recent_n < 1:
        return None  # need an earlier portion to compare against
    spread = (df["high"] - df["low"]).to_numpy(dtype=float)[-range_n:]
    recent_vol = spread[-recent_n:].mean()
    earlier_vol = spread[:-recent_n].mean()
    if not (earlier_vol > 0) or pd.isna(recent_vol) or pd.isna(earlier_vol):
        return None
    contraction = 1.0 - (recent_vol / earlier_vol)  # > 0 means recent is tighter
    if contraction <= 0:
        return None  # expanding or flat → not a coil
    magnitude = _clip01(contraction / CONTRACTION_FULL_SCALE) * 100.0
    return magnitude if near_support else -magnitude


# --- small helpers ---


def _recent_atr(df: pd.DataFrame, window: int) -> float | None:
    """Average true range (gap-aware) over the last ``window`` bars, in price units — the
    volatility the planner's ATR stop method sizes against. ``None`` on a degenerate series."""
    n = min(int(window), len(df))
    if n < 1:
        return None
    high = df["high"].iloc[-n:]
    low = df["low"].iloc[-n:]
    prev_close = df["close"].shift(1).iloc[-n:]
    true_range = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr = float(true_range.mean())
    return atr if (pd.notna(atr) and atr > 0) else None


def _last(features: pd.DataFrame, column: str) -> float:
    return float(features[column].iloc[-1])


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _clip01(value: float) -> float:
    return _clip(value, 0.0, 1.0)
