# Wyckoff Methodology Reference

The bridge between Wyckoff/VSA theory and `strategies/wyckoff.py`. This is the document the
implementer reads to turn discretionary chart-reading into deterministic, testable rules.

**Two rules govern this entire document:**

1. **Every threshold is relative, never absolute.** No dollar amounts, no raw share counts.
   All "high/low/narrow/wide" terms are defined against *each stock's own rolling
   distribution* (see §1). Config holds **ratios and percentiles**, not absolute values.
2. **Discretionary numbers are tunables, not truths.** Anywhere a specific number appears
   below (e.g. "lower than the previous 2 bars"), it is a *candidate* drawn from a source
   and marked `[TUNABLE: …]`. It must be (a) verified against the source and (b) treated as
   a calibration parameter, not gospel. Where Wyckoff/VSA schools disagree, the choice made
   here is noted with `[CHOICE: …]`.

> **Sourcing note.** Content below is stubbed from VSA-oriented Wyckoff material (a
> highly-regarded long-form video lecture and Villahermosa's *The Wyckoff Methodology in
> Depth*). Numeric specifics from a *summary* of a source must be confirmed against the
> source itself before they are trusted — summaries tend to invent precision the original
> did not intend. **Stubs marked `[VERIFY]` are not yet confirmed.**

---

## 1. Per-stock normalization (the foundation — build this first)

No two stocks share a volume or volatility profile, so absolute thresholds are meaningless
across a universe. Before any pattern logic runs, each ticker is normalized into its own
statistical frame. `strategies/wyckoff.py` operates on these **relative features**, not raw
OHLCV.

This is a distinct step (a feature/normalization pass) producing a feature frame per
ticker/timeframe. Suggested features, all computed on a rolling window:

| Feature | Definition (rolling, per stock) | Notes |
|---|---|---|
| `volume_ratio` | bar volume ÷ rolling **median** volume | Median, not mean — volume is right-skewed; one climax bar inflates the mean and masks the next. |
| `volume_pctile` | percentile rank of bar volume within the window | More robust than a fixed multiple for "high/low volume" calls. |
| `spread_atr` | bar range (high−low) ÷ rolling ATR | The relative-spread measure. "Narrow"/"wide" are defined on this, never in dollars. |
| `spread_pctile` | percentile rank of bar range within the window | Alternative/robustness check for spread calls. |
| `close_position` | (close − low) ÷ (high − low) | Where in the bar the close landed (0=low, 1=high). Drives effort/result and No Demand/Supply. |

**Baseline window.** `[TUNABLE: 20–30 bars]`. Trade-off: shorter = more responsive but
noisier; longer = stabler but slow to adapt. A medium window with **median/percentile**
(not mean) is the default.

**Regime caveat (do not over-engineer now).** After an earnings-driven volatility
expansion, a stock's ATR baseline stays inflated for weeks, desensitizing "narrow spread"
detection exactly when structure matters. v1 accepts this and uses a medium rolling window;
a regime-aware baseline is explicitly out of scope. Flag the baseline window as a
calibration parameter and move on.

**Config implication.** Wyckoff knobs become ratios/percentiles, e.g.
`high_volume_ratio: 2.0`, `narrow_spread_atr: 0.5`, `volume_pctile_high: 80`. These are the
values calibration will tune against `signals.csv`.

---

## 2. Signal definitions (VSA markers → relative-feature conditions)

Each signal below is written as a condition over §1 features. Numbers are `[TUNABLE]`
candidates to confirm against the source.

### 2.1 Effort vs. Result (divergence)
- **Idea:** result (price movement) should match effort (volume). A mismatch signals
  absorption or exhaustion.
- **Condition (candidate):** `volume_ratio` high **and** `spread_atr` low (high effort, no
  result) → absorption/exhaustion flag. `[VERIFY]` `[TUNABLE: high = volume_ratio ≥ 2.0;
  low = spread_atr ≤ 0.5]`
- **Directional read** depends on location in the range and `close_position`.

### 2.2 No Demand / No Supply
- **No Demand (candidate):** narrow-range *up* bar with volume lower than the previous N
  bars, near resistance → weak demand. `[VERIFY]` `[TUNABLE: N = 2]` `[CHOICE: "previous 2
  bars" is one practitioner's specific; confirm and consider "below rolling median" as the
  more universe-stable form]`
- **No Supply (candidate):** narrow-range *down* bar with volume lower than the previous N
  bars, near support → weak supply.
- These are clean, objective support/resistance tests — high value, low complexity.

### 2.3 Climax (selling / buying)
- **Selling/Buying Climax (candidate):** the highest `volume_pctile` bar within a localized
  window, followed by a sharp reversal reaction. `[VERIFY]` `[TUNABLE: window length;
  "sharp reaction" magnitude]`
- **Preliminary stop (candidate):** a sudden volume spike on a narrow-range bar during a
  clear trend (an early warning before the climax). `[VERIFY]`
- Climax levels set the horizontal boundaries (automatic rally/reaction) used for range
  detection in §3.

### 2.4 Spring / Upthrust (structural extremes)
- **Spring (candidate):** price makes a new low *relative to the lookback range* then closes
  back inside the range, ideally on a volume/spread profile consistent with §2.1.
  `[TUNABLE: lookback; max bars to close back inside; spring_wick_pct already in config]`
- **Upthrust:** mirror image at the range high.
- Bonus conviction when the false-break's volume behavior corroborates (e.g. spring on
  diminishing supply, recovery on rising demand).

---

## 3. Trading range & structure

- **Range boundaries** are anchored off climax-driven automatic rally/reaction levels (§2.3)
  rather than arbitrary highs/lows. `[VERIFY]`
- Range validity gates (`range_max_width_pct`, `min_range_bars`) already in config; confirm
  per-timeframe values (see open question on Daily-vs-Weekly lookback semantics).
- **Phase context (used as bias, not as a hard label):** only treat accumulation as
  meaningful after a prior downtrend, distribution after a prior uptrend. The methodology's
  own guidance is to favor entries in later phases (C/D/E) and to treat the Phase-C false
  breakout (spring/upthrust) as highest reward — *with volume confirmation*. We encode this
  as scoring bias, consistent with "score conviction, don't assert the label."

---

## 4. Consistency principles (encode as scoring bias / guards)

These are interpretive guards from the source, translated to scoring rules:

- **Context over patterns.** A signal unaccompanied by preceding trend-exhaustion context
  (e.g. a climax) is down-weighted — "a pattern without context is noise."
- **The harmonic rule.** In a healthy trend, volume rises on impulse moves and falls on
  corrections; a break (volume higher on a correction) flags a failing trend. Candidate
  input to trend-context scoring. `[VERIFY]` `[TUNABLE]`
- **Flexibility over rigid labels.** When structure doesn't fit a textbook cycle, fall back
  to the raw VSA absorption/exhaustion read rather than forcing a phase label. This is the
  core justification for scoring conviction instead of emitting a definitive phase.

---

## 5. Open methodology decisions (resolve during build)

- [ ] Confirm every `[VERIFY]` number against the actual source (video/book), not the summary.
- [ ] For each `[CHOICE]`, decide: adopt the practitioner's specific value, or use the more
      universe-stable rolling-median/percentile form.
- [ ] Decide which signals contribute to which Wyckoff sub-score (`range_structure`,
      `volume_behavior`, `spring_upthrust`, `confirmation`) and with what relative weight.
- [ ] Per-timeframe parameter values (Daily vs Weekly) for every lookback/window above.
- [ ] Whether `close_position` and `spread_pctile` are both needed or one is redundant
      (resolve empirically once `signals.csv` has data).
