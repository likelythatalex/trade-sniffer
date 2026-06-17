# Appendix — Concepts, Acronyms & Implementation Status

<!--
CHANGE-MARK (refinement pass, aligned to SPEC §6.1/§6.4):
  • "Climax" entry: implementation line corrected — climax feeds the volume sub-score and
    range *context*, it does NOT set the range boundary in v1 (the band does); climax-anchored
    boundaries are FUTURE.
  • "Trading Range / Phases" entry: boundary method stated as the v1 support/resistance band.
  Final parameterization pass:
  • "No Demand / No Supply" entry: rolling-median form + near-extreme (range thirds).
  • "Spring / Upthrust" entry: cites spring_lookback / spring_snapback_bars / spring_wick_pct.
  • §D calibration table: added range_extreme_fraction, spring_lookback/snapback, trend_lookback,
    no_demand_supply_median_window; "prev N" reframed as source-note only.
  Status tags unchanged (still pre-implementation). No other entries altered.
-->

A living reference for the trading/market concepts this project uses, what they mean in
plain terms, and **how (or whether) this codebase implements them.** Updated with each
version. Think of it as a README for the *domain*, not the code.

## How to use this doc

- **Every entry has a status tag.** Keep them accurate — this doc's value is being honest
  about what's real vs. planned.
- **When you implement something, update its entry** (status + the "How it's implemented
  here" line + the module/config reference) in the same change. An entry that says
  `IMPLEMENTED` but points at code that doesn't exist is worse than no entry.
- **When you add a new concept** (even one not built yet), add it as `PLANNED` so the intent
  is captured.

### Status tags
| Tag | Meaning |
|---|---|
| `IMPLEMENTED` | Built, tested, in use. References a real module/config key. |
| `PARTIAL` | Some of it works; caveats noted in the entry. |
| `PLANNED` | Decided and specced, not built. |
| `FUTURE` | Acknowledged direction, deliberately out of current scope. |
| `EXCLUDED` | Considered and intentionally left out (with the reason). |

### Status legend for THIS version
> **Version: implementation in progress.** `config.py` (load/validate/per-timeframe
> resolution) and `features.py` (Per-Stock Normalization, below) have landed and are
> `IMPLEMENTED`; the remaining domain concepts are still `PLANNED`/`FUTURE`/`EXCLUDED`.
> Flip items to `IMPLEMENTED`/`PARTIAL` as they land. Do not leave this legend stale.

---

## A. Acronyms (quick reference)

| Acronym | Expansion | One-liner |
|---|---|---|
| **VSA** | Volume Spread Analysis | Reading supply/demand from the relationship between volume, bar range (spread), and close. The objective, automatable core of the Wyckoff read used here. |
| **OHLCV** | Open, High, Low, Close, Volume | The raw per-bar price data everything is computed from. |
| **ATR** | Average True Range | Rolling measure of a stock's typical bar range; the basis for "narrow/wide spread" relative to the stock itself. |
| **RS** | Relative Strength (vs. an index) | Is the stock out/under-performing SPY? A confirmation input. (Not RSI — see below.) |
| **RSI** | Relative Strength Index | A bounded momentum oscillator. *Not* used as a core signal in v1; listed to avoid confusion with RS. |
| **MTF** | Multi-Timeframe | Agreement of a signal across Daily and Weekly raises conviction. |
| **TF** | Timeframe | Daily or Weekly in this project. |
| **TRF** | Trade Reporting Facility | Where off-exchange (incl. dark pool) trades are reported; relevant only to the FUTURE dark-pool idea. |
| **ATS** | Alternative Trading System | Regulatory term covering dark pools; FINRA publishes aggregate ATS volume. FUTURE input. |
| **TV** | TradingView | Source of the embedded interactive charts in the dashboard. |

---

## B. Core market concepts → how this project uses them

### Wyckoff Methodology
- **Plain meaning:** A framework treating the market as a contest between informed
  institutions and uninformed retail, expressed through cycles of accumulation (institutions
  buying quietly) and distribution (selling quietly) before markup/markdown.
- **How it's implemented here:** As *structural fingerprint detection + a conviction score*,
  **not** definitive phase labeling. See `docs/wyckoff_methodology.md`.
- **Status:** `PLANNED` (`strategies/wyckoff.py`).

### Accumulation / Distribution
- **Plain meaning:** A trading range where informed money is building a position
  (accumulation → bullish) or unloading one (distribution → bearish).
- **How it's implemented here:** Direction emitted per ticker/timeframe; accumulation = long
  candidate, distribution = short candidate. Both actionable.
- **Status:** `PLANNED`.

### Effort vs. Result
- **Plain meaning:** Volume is effort, price movement is result; a mismatch (big effort, no
  result) signals absorption or exhaustion.
- **How it's implemented here:** `volume_ratio` high + `spread_atr` low over normalized
  features. See methodology §2.1.
- **Status:** `PLANNED` (`features.py` + `strategies/wyckoff.py`).

### No Demand / No Supply
- **Plain meaning:** A narrow-range bar on low volume into resistance (no demand) or support
  (no supply) — a weak push that often fails.
- **How it's implemented here:** Relative-feature condition — narrow `spread_atr` + volume
  **below the rolling median** (`no_demand_supply_median_window`), directional, near a range
  extreme (`range_extreme_fraction` thirds, SPEC §6.1). Median form chosen over "prev N bars"
  for universe stability. Methodology §2.2.
- **Status:** `PLANNED`.

### Climax (selling / buying)
- **Plain meaning:** A volume peak that marks exhaustion of a move, often preceding a
  reversal and the start of a range.
- **How it's implemented here:** Rolling `volume_pctile` max + reversal check. Climax feeds
  the `volume_behavior` sub-score and provides *context* for range scoring; in v1 it does
  **not** set the range boundary — the support/resistance band does (SPEC §6.1). Climax-
  anchored boundaries are a FUTURE refinement. Methodology §2.3.
- **Status:** `PLANNED`.

### Spring / Upthrust
- **Plain meaning:** A false breakdown below support (spring, bullish) or false breakout
  above resistance (upthrust, bearish) that snaps back into the range — a classic Wyckoff
  trap of the uninformed.
- **How it's implemented here:** New low/high vs. `spring_lookback` range + close back inside
  within `spring_snapback_bars` on a `spring_wick_pct` rejection wick, volume corroboration
  bonus. Methodology §2.4.
- **Status:** `PLANNED`.

### Trading Range / Phases (A–E)
- **Plain meaning:** The consolidation where accumulation/distribution happens; Wyckoff
  subdivides it into phases A–E. Later phases (C/D/E) are where entries are favored;
  Phase-C spring/upthrust is the highest-reward, highest-risk spot.
- **How it's implemented here:** Range **boundaries defined by a support/resistance band**
  over `range_lookback` (v1 — SPEC §6.1), validated by `range_max_width_pct` /
  `min_range_bars`. Phase context used as *scoring bias* (e.g. favor later-phase setups), not
  asserted as a hard label. Climax-anchored boundaries are FUTURE.
- **Status:** `PARTIAL`-by-design — range yes, full phase labeling intentionally not.

### Per-Stock Normalization (relative features)
- **Plain meaning:** "High volume" or "narrow spread" only mean something relative to a
  given stock's own history; absolute thresholds are meaningless across a universe.
- **How it's implemented here:** `features.compute_features` produces `volume_ratio`
  (vs rolling **median** volume), `volume_pctile`, `spread_atr` (bar range ÷ ATR, where
  ATR = rolling mean of True Range), `spread_pctile`, and `close_position` on a trailing
  rolling window that includes the current bar. Degenerate bars (zero range, zero
  ATR/median) emit NaN per-feature (no coercion); bars before `baseline_window` are
  unscored. Config holds ratios/percentiles, not absolutes. Methodology §1.
- **Status:** `IMPLEMENTED` (`features.py`, tested in `tests/test_features.py`).
  **Foundational — built first.** Thresholds that *consume* these features remain
  `[TUNABLE]`; the features themselves are done.

### Relative Strength vs. SPY (RS)
- **Plain meaning:** A stock holding up better than the index on weak tape (or worse on
  strong tape) suggests informed positioning.
- **How it's implemented here:** Confirmation input to the Wyckoff `confirmation` sub-score;
  SPY always fetched, exempt from the liquidity gate.
- **Status:** `PLANNED`.

### Volatility Contraction ("the coil")
- **Plain meaning:** Narrowing range/volatility inside a consolidation often precedes the
  expansion move.
- **How it's implemented here:** Bollinger Band width or ATR contraction as a confirmation
  input.
- **Status:** `PLANNED`.

### Multi-Timeframe (MTF) Agreement
- **Plain meaning:** A setup confirmed on two timeframes is more reliable than on one.
- **How it's implemented here:** The running TF reads the *stored* most-recent result of the
  other TF (no recompute) and applies a conviction bonus. Cold-start default = neutral.
- **Status:** `PLANNED` (`state.py`).

### Liquidity Filter
- **Plain meaning:** Volume-based signals are only trustworthy on liquid names; thin stocks
  produce noise and aren't tradeable at size.
- **How it's implemented here:** 20-day average dollar-volume + min-price gate at scan time;
  illiquid names skipped with a logged reason. This is a universe-eligibility filter, so an
  **absolute** floor is the correct tool here (the relative-threshold rule governs *signal*
  thresholds, not universe gating).
- **Status:** `PLANNED` (`universe.py`).

### Conviction Score / Confirmation Stacking
- **Plain meaning:** Rather than a yes/no call, rank candidates 0–100; independent signals
  agreeing should raise the score.
- **How it's implemented here:** Per-strategy `StrategyResult` scores combined in
  `combiner.py`. v1 = one strategy, so composite = Wyckoff score.
- **Status:** `PLANNED` (stacking machinery present, only one strategy wired).

### Signal Correlation Awareness
- **Plain meaning:** Stacked signals only add information if they're *independent*; three
  trend-flavored signals agreeing is one signal counted thrice.
- **How it's implemented here:** Not yet. Designated home is `combiner.py`, using
  per-strategy scores logged in `signals.csv` to measure pairwise correlation later.
- **Status:** `FUTURE`.

### Survivorship Bias
- **Plain meaning:** Testing only on stocks that exist today overstates results (you've
  excluded everything that failed).
- **How it's implemented here:** Live scanning uses today's universe (correct). Any future
  backtest must use a point-in-time universe — flagged, not yet built.
- **Status:** `FUTURE` (backtester).

### Lookahead Bias
- **Plain meaning:** Accidentally using information not available at decision time, making
  results unrealistically good.
- **How it's implemented here:** Only closed bars are evaluated; an acceptance test asserts
  bar-N evaluation is unchanged by appending bar N+1.
- **Status:** `PLANNED` (guard + test).

### Dark Pool / Off-Exchange Prints
- **Plain meaning:** Large trades executed away from public exchanges, reported after the
  fact. Often marketed as "smart money" signal.
- **How it's implemented here:** Not used. Assessed as a weak, easily-overtrusted signal
  that largely overlaps the volume read Wyckoff already does. If ever added, it goes in as
  its *own* strategy whose independent value is *measured* before it's trusted.
- **Status:** `EXCLUDED` (with revisit path noted).

---

## C. Data & infrastructure concepts

### Data Quality / Bad-Bar Handling
- **Plain meaning:** Free data has errors (zero-volume bars, spikes, bad split adjustment); a
  single bad bar can fake a volume climax.
- **How it's implemented here:** A conservative detect → repair-the-obvious → else-exclude
  step that *never invents data* and logs what it touched. Runs **before** normalization so
  bad bars don't poison rolling baselines. Pipeline: `data → data_quality → features →
  strategy`.
- **Status:** `PLANNED` (`data_quality.py`).

### Resampling vs. Native Timeframe
- **Plain meaning:** Weekly bars can be aggregated from daily or fetched directly; the choice
  affects history depth and accuracy.
- **How it's implemented here:** Weekly fetched natively (`interval='1wk'`) to avoid
  resampling artifacts and history-depth math problems.
- **Status:** `PLANNED`.

### State / Dedup
- **Plain meaning:** Re-alerting the same still-valid setup every run is noise; new and
  newly-failed setups are what's actionable.
- **How it's implemented here:** Prior-run state classifies each ticker
  new/continuing/failed; notifications fire only on new + failed transitions.
- **Status:** `PLANNED` (`state.py`).

### Embedded Charts / Dashboard
- **Plain meaning:** Find and inspect candidates in one place rather than exporting to
  another app.
- **How it's implemented here:** Self-contained-single-file HTML report per timeframe with
  free TradingView embed widgets (attribution kept); ranked by score.
- **Status:** `PLANNED` (`report.py`).

### Notification Surface
- **Plain meaning:** Where you get pinged about new setups.
- **How it's implemented here:** Discord webhook (v1) with top tickers + link to the report.
  Telegram and in-channel static chart previews are future options.
- **Status:** `PLANNED` (Discord); `FUTURE` (Telegram, chart-image previews).

---

## D. Calibration parameters (the tunables)

These are the values that should be *calibrated against accumulated `signals.csv` data*, not
guessed once and forgotten. Listed here so they're not scattered. All currently `PLANNED`.

| Parameter | Lives in | What it controls |
|---|---|---|
| baseline window (bars) | `features.py` / config | Rolling window for volume/spread normalization |
| `high_volume_ratio`, `volume_pctile_high` | config (per-TF) | What counts as "high volume" |
| `narrow_spread_atr`, `spread_pctile_*` | config (per-TF) | What counts as "narrow/wide spread" |
| `no_demand_supply_median_window` | config (per-TF) | Window for "volume below rolling median" (median form; "prev N" is source-note only) |
| climax window + reaction magnitude (`climax_window`, `climax_reaction_atr`) | config (per-TF) | Climax detection sensitivity |
| `spring_lookback`, `spring_snapback_bars`, `spring_wick_pct` | config (per-TF) | Structural-extreme (spring/upthrust) detection |
| `range_extreme_fraction` | config (per-TF) | "Near support/resistance" = lower/upper fraction of the range (seed 0.33) |
| `trend_lookback` | config (per-TF) | Prior up/down-trend window for trend-context (price vs. MA / net change) |
| range validity (`range_max_width_pct`, `min_range_bars`) | config (per-TF) | What counts as a valid trading range |
| sub-score weights | config | How signals combine into the Wyckoff score |
| intra-sub-score signal weights | code (seed) / future config | Relative weight of signals within a sub-score (start equal; calibrate) |
| `watchlist_threshold` | config | Score cutoff for making the report |

---

## E. Changelog of this doc
- *pre-implementation:* initial version; all domain entries `PLANNED`/`FUTURE`/`EXCLUDED`.
  Flip to `IMPLEMENTED`/`PARTIAL` as code lands, and update the version legend in the header.
- *pre-implementation (refinement):* Climax + Trading Range entries reworded so the v1 range
  boundary is the support/resistance band (climax = context/scoring, not boundary; climax-
  anchored boundaries FUTURE); added intra-sub-score weights to §D. Status tags unchanged.
- *pre-implementation (parameterization):* No Demand/Supply → rolling-median form + range-thirds
  near-extreme; spring parameterized (spring_lookback/snapback); §D gains range_extreme_fraction,
  trend_lookback, no_demand_supply_median_window. Trend-context = simple price-vs-MA (harmonic
  rule FUTURE). Status tags unchanged.
