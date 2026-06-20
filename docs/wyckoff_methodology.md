# Wyckoff Methodology Reference

<!--
CHANGE-MARK (refinement pass, aligned to SPEC.md §6.1/§6.4 decisions):
  • §3 reworded: v1 range BOUNDARY is the support/resistance band (per SPEC §6.1). Climax-
    anchored boundaries demoted to [FUTURE]; climax informs scoring *within* the range only.
  • §5: signal → sub-score mapping pinned as a first-pass (weights = calibration seeds),
    mirroring SPEC §6.4. Other open items unchanged.
  Final parameterization pass (aligned to SPEC §4.2 config + §6/§7):
  • §2.2: No Demand/Supply [CHOICE] resolved to the rolling-median form ("prev N" kept as a
    source note only); "near support/resistance" bound to the §6.1 range thirds.
  • §2.4: spring parameterized with spring_lookback + spring_snapback_bars (+ spring_wick_pct).
  • §4: v1 trend-context = simple trend_lookback price-vs-MA; harmonic (leg-segmentation) rule
    demoted to [FUTURE]. §5 checklist updated.
  Methodology numbers remain verifiable stubs ([VERIFY]/[TUNABLE]/[CHOICE]); seeds are
  calibration starting points, nothing here is hardened into an asserted truth.
-->

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

> **Sourcing note.** Content below is grounded in VSA-oriented Wyckoff material (a
> highly-regarded long-form video lecture and Villahermosa's *The Wyckoff Methodology in
> Depth*, hereafter "the book"). The book's **event/structure definitions** have now been
> read against the source and the items below cite it (`[SOURCE: book]`). **Numbers stay
> `[TUNABLE]` regardless** — confirming a definition does not harden its thresholds into
> truths; those are still calibration seeds against `signals.csv`. Items still marked
> `[VERIFY]` are not yet confirmed.

---

## 0. Mapping to the Wyckoff canon (scope & fidelity)

This tool detects the **structural fingerprints** of accumulation/distribution and emits a
0–100 conviction score. It deliberately does **not** label phases or call individual events
by name in its output. This table is the honest bridge between the book's vocabulary and
what the code actually computes — read it to know what we model, what we approximate, and
what we leave out on purpose.

| Book concept (Villahermosa) | Our component | Fidelity |
|---|---|---|
| Law of Effort vs Result (Ch 9) | `volume_behavior` effort/result + No Demand/Supply (§2.1–2.2) | Faithful |
| Law of Supply & Demand (Ch 7) | range-location + absorption reads (§2, §3) | Faithful (implicit) |
| Law of Cause & Effect (Ch 8) — P&F count | **not modelled** — `trade_plan` targets are range/ATR-based, not a cause projection | **Gap** `[FUTURE]` |
| Selling/Buying Climax — SC/BC (Event #2) | `_score_climax` (spike + reaction); now marked on the chart | Faithful |
| Spring / Shakeout (Event #5) | `detect_spring_upthrust` spring side (§2.4) | Faithful (generic; not the book's 3 sub-types) |
| **UTAD** (Phase-C distribution shake) | `detect_spring_upthrust` upthrust side — this is **UTAD**, not the minor Phase-B "UT/UA" | Faithful (see §2.4 note) |
| Trading range bounded by **Creek / ICE** | support/resistance band over `range_lookback` (§3) | Simplified — band, not AR-anchored Creek/ICE `[FUTURE]` |
| Preliminary Support/Supply — PS/PSY (Event #1) | **not modelled** | Out of scope |
| Automatic Rally/Reaction — AR (Event #3) | **not modelled** as a named level (would anchor Creek/ICE) | Out of scope `[FUTURE]` |
| Secondary Test — ST (Event #4) | **not modelled** as a distinct event | Out of scope |
| Breakout — SOS / SOW, Jump-Across-the-Creek (Event #6) | **not modelled** — we flag *within-range* setups | Out of scope `[FUTURE]` |
| Confirmation — LPS / LPSY, BUEC (Event #7) | **not modelled** | Out of scope `[FUTURE]` |
| Phases A–E | **not modelled** — we score conviction, never assert a phase label | Out of scope by design |

**Why this scope:** the book itself frames Phase-C events (Spring/UTAD) as the
highest-reward, and treats phase labelling as discretionary. We encode the high-value,
objectively-detectable parts (range location + volume behaviour + the Phase-C shake +
climax context) as a conviction score, and leave the discretionary labelling to the human
reviewer. The "Out of scope" rows are candidates for future strategies/refinements, not
errors. See [appendix.md](appendix.md) for implementation status.

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
  result) → absorption/exhaustion flag. `[SOURCE: book — Law of Effort vs Result, Ch 9]`
  `[TUNABLE: high = volume_ratio ≥ 2.0; low = spread_atr ≤ 0.5]`
- **Directional read** depends on location in the range and `close_position`.

### 2.2 No Demand / No Supply
- **No Demand:** narrow-range *up* bar with volume **below the rolling median**
  (`no_demand_supply_median_window`), **near resistance** → weak demand.
- **No Supply:** narrow-range *down* bar with volume below the rolling median, **near
  support** → weak supply.
- `[CHOICE — resolved]` We implement the **rolling-median** form (volume below the rolling
  median), not "lower than the previous N bars." The median form is the universe-stable
  version, consistent with the §1 normalization principle. The source's original
  ("previous 2 bars", `[TUNABLE: N = 2]`) is retained here only for traceability; it is
  **not** what ships. `[VERIFY]` the window against the source.
- **"Near support/resistance"** is the §6.1 range-position output (lower/upper
  `range_extreme_fraction` of the range, seed 0.33) — not a separate threshold (see SPEC §6.1).
- These are clean, objective support/resistance tests — high value, low complexity.

### 2.3 Climax (selling / buying) — SC / BC, Event #2
- **Selling/Buying Climax:** the highest-volume bar within a localized window, followed by a
  sharp reversal reaction. `[SOURCE: book — Event #2, Climax]` `[TUNABLE: window length;
  "sharp reaction" magnitude]` The reaction requirement is deliberate: a bare volume spike
  is not a climax until price reacts off it (`_score_climax`).
- **Preliminary stop (candidate):** a sudden volume spike on a narrow-range bar during a
  clear trend (an early warning before the climax — the book's PS/PSY, Event #1). `[VERIFY]`
  Not modelled in v1 (see §0).
- Climax characterization feeds the `volume_behavior` sub-score and provides *context* for
  range scoring (§3). In v1 it does **not** set the range boundary (the band does). A
  confirmed climax bar is **marked on the dashboard chart** (SC/BC glyph).

### 2.4 Spring / Upthrust (structural extremes) — Event #5, Shaking
- **Spring:** within `spring_lookback` bars, price makes a new low *relative to
  the lookback range* then closes back inside the range within `spring_snapback_bars`,
  ideally on a volume/spread profile consistent with §2.1. `[SOURCE: book — Event #5, Spring]`
  `[TUNABLE: spring_lookback; spring_snapback_bars; spring_wick_pct — all per-TF config seeds]`
  We detect a single generic spring; the book's three sub-types (Spring / Test Spring /
  Terminal Shakeout) are not separately classified `[FUTURE]`.
- **Upthrust = UTAD.** Mirror image at the range high. **Naming precision:** what we label
  "upthrust" is the book's **UTAD** (Upthrust After Distribution) — the *Phase-C* shake that
  breaks the Phase A/B highs and is the true mirror of a Spring. The book reserves bare
  "Upthrust/UT" for a *minor Phase-B* test of the AR high, which we do not model. The chart
  marker therefore reads **UTAD**, not "Upthrust". `[SOURCE: book — UTAD, Event #5 / Phase C]`
- Bonus conviction when the false-break's volume behavior corroborates (e.g. spring on
  diminishing supply, recovery on rising demand).

---

## 3. Trading range & structure

- **Range boundaries (v1):** the trading range is a support/resistance **band** detected over
  `range_lookback` — this is the v1 boundary method (see SPEC §6.1). In the book's vocabulary
  the resistance is the **Creek** and the support the **ICE**; the chart labels the band lines
  accordingly. `[FUTURE]` Anchoring the boundaries off the climax-driven Automatic Rally/
  Reaction levels (§2.3) — the book's *true* Creek/ICE definition — is a planned refinement,
  **not v1**. In v1, climax (§2.3) informs *scoring within* the range (range_structure context
  + volume_behavior), it does not define the boundary. `[VERIFY]` (for the future method)
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
- **Trend context (v1, simple).** Over `trend_lookback` bars, classify the prior trend by
  price vs. a rising/falling N-period MA (equivalently the sign of net change). Accumulation
  is favored after a prior downtrend, distribution after a prior uptrend; penalize setups
  lacking the preceding move. `[TUNABLE: trend_lookback]`
- **The harmonic rule `[FUTURE]`.** The fuller version — in a healthy trend volume rises on
  impulse legs and falls on corrections, and a break (volume higher on a correction) flags a
  failing trend — needs impulse/correction **leg segmentation** and is **not v1**. It is a
  planned refinement to trend-context scoring. `[VERIFY]` `[TUNABLE]`
- **Flexibility over rigid labels.** When structure doesn't fit a textbook cycle, fall back
  to the raw VSA absorption/exhaustion read rather than forcing a phase label. This is the
  core justification for scoring conviction instead of emitting a definitive phase.

---

## 5. Open methodology decisions (resolve during build)

- [ ] Confirm every `[VERIFY]` number against the actual source (video/book), not the summary.
- [x] **No Demand/Supply `[CHOICE]` — resolved:** rolling-median form adopted (§2.2); the
      source's "previous N bars" is kept as a traceability note only. For any *other*
      `[CHOICE]` that arises, default to the universe-stable rolling-median/percentile form.
- [x] **Trend context — resolved for v1:** simple `trend_lookback` price-vs-MA measure (§4);
      the harmonic (impulse/correction leg) rule is `[FUTURE]`.
- [x] **Signal → sub-score mapping — pinned (first-pass; weights are calibration seeds, NOT
      final).** Each signal feeds exactly one sub-score; within a sub-score the signals start
      at *equal* weight, each `[TUNABLE: calibration seed]` to be tuned against `signals.csv`.
      Mirrors SPEC §6.4:
      - `volume_behavior` (35): Effort-vs-Result (§2.1), No Demand / No Supply (§2.2), climax
        volume characterization (§2.3)
      - `spring_upthrust` (20): spring / upthrust + volume corroboration (§2.4)
      - `range_structure` (25): range validity/quality + climax as *context* within the range
      - `confirmation` (20): RS-vs-SPY, volatility contraction, MTF, trend context
      Still open: the *intra*-sub-score weights (start equal; calibrate). The four top-level
      `sub_weights` (25/35/20/20) live in config and are themselves tunable.
- [ ] Per-timeframe parameter values (Daily vs Weekly) for every lookback/window above.
- [ ] Whether `close_position` and `spread_pctile` are both needed or one is redundant
      (resolve empirically once `signals.csv` has data).
