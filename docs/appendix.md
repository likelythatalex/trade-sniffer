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
- **This doc tracks *concept* status, not the work backlog.** What to build next and in what
  order lives in `ROADMAP.md` (the single source of truth for prioritization); keep the two
  in sync when an item lands.

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
- **Status:** `PARTIAL` (`strategies/wyckoff.py`, tested in `tests/test_wyckoff.py`).
  First-pass scoring implemented (range/volume/spring + all four confirmation inputs: trend
  context, RS-vs-SPY, volatility contraction, MTF); a valid range is a precondition. Sub-score
  weights and per-signal thresholds remain calibration seeds.

### Accumulation / Distribution
- **Plain meaning:** A trading range where informed money is building a position
  (accumulation → bullish) or unloading one (distribution → bearish).
- **How it's implemented here:** Direction emitted per ticker/timeframe; accumulation = long
  candidate, distribution = short candidate. Both actionable.
- **Status:** `IMPLEMENTED` (`strategies/wyckoff.py` + `combiner.py`): a signed composite
  yields `accumulation`/`distribution`/`none` plus a 0–100 conviction score.

### Effort vs. Result
- **Plain meaning:** Volume is effort, price movement is result; a mismatch (big effort, no
  result) signals absorption or exhaustion.
- **How it's implemented here:** `volume_ratio` high + `spread_atr` low over normalized
  features, directional by range location (`score_volume_behavior`). See methodology §2.1.
- **Status:** `IMPLEMENTED` (`features.py` + `strategies/wyckoff.py`).

### No Demand / No Supply
- **Plain meaning:** A narrow-range bar on low volume into resistance (no demand) or support
  (no supply) — a weak push that often fails.
- **How it's implemented here:** Relative-feature condition — narrow `spread_atr` + volume
  **below the rolling median** (`no_demand_supply_median_window`), directional, near a range
  extreme (`range_extreme_fraction` thirds, SPEC §6.1). Median form chosen over "prev N bars"
  for universe stability. Realized as `volume_ratio < 1` (below the rolling median) on a
  narrow `spread_atr` bar near the matching extreme. Methodology §2.2.
- **Status:** `IMPLEMENTED` (`strategies/wyckoff.py`, `score_volume_behavior`).

### Climax (selling / buying)
- **Plain meaning:** A volume peak that marks exhaustion of a move, often preceding a
  reversal and the start of a range.
- **How it's implemented here:** `wyckoff._score_climax` requires a volume spike
  (`volume_ratio` ≥ `high_volume_ratio` in the recent `climax_window`, at a range extreme)
  **and** a subsequent sharp reaction of ≥ `climax_reaction_atr` × ATR away from the climax
  bar's extreme — a spike with no reaction abstains. Feeds the `volume_behavior` sub-score.
  Climax does **not** set the range boundary — the band does (SPEC §6.1); climax-anchored
  boundaries are FUTURE; the `volume_pctile` alternative remains deferred. Methodology §2.3.
- **Status:** `IMPLEMENTED` (`strategies/wyckoff.py`, tested in `tests/test_wyckoff.py`):
  spike + reaction; `volume_pctile` alternative still deferred.

### Spring / Upthrust
- **Plain meaning:** A false breakdown below support (spring, bullish) or false breakout
  above resistance (upthrust, bearish) that snaps back into the range — a classic Wyckoff
  trap of the uninformed.
- **How it's implemented here:** New low/high vs. the established band over `spring_lookback`
  + close back inside within `spring_snapback_bars` is the GATE (`detect_spring_upthrust`).
  Given detection, the magnitude scales from `SPRING_BASE_FRACTION` up to full with two
  equal-weight confirmations on the false-break bar: a rejection wick (`spring_wick_pct`) and
  volume corroboration (above-median `volume_ratio`). Methodology §2.4.
- **Status:** `IMPLEMENTED` (`strategies/wyckoff.py`, tested in `tests/test_wyckoff.py`):
  break+snapback + wick% + volume corroboration. Volume corroboration is a simple above-median
  seed (the fuller impulse/correction-leg rule stays `[FUTURE]`).

### Trading Range / Phases (A–E)
- **Plain meaning:** The consolidation where accumulation/distribution happens; Wyckoff
  subdivides it into phases A–E. Later phases (C/D/E) are where entries are favored;
  Phase-C spring/upthrust is the highest-reward, highest-risk spot.
- **How it's implemented here:** Range **boundaries defined by a support/resistance band**
  over `range_lookback` (`detect_trading_range`, SPEC §6.1), validated by `range_max_width_pct`
  / `min_range_bars`; a valid range is a **precondition** for any directional call. Phase
  context used as *scoring bias*, not a hard label. Climax-anchored boundaries are FUTURE.
- **Status:** `PARTIAL`-by-design (`strategies/wyckoff.py`) — range band implemented; full
  phase labeling intentionally not.

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
- **How it's implemented here:** SPY's close is batch-fetched once per timeframe
  (`scanner._benchmark_close`, fail-soft → RS abstains) and passed into the strategy via
  `StrategyContext.benchmark_close`. `wyckoff._relative_strength` scores the stock's return
  minus SPY's over `trend_lookback` (scaled by `RS_FULL_SCALE`, a `[TUNABLE]` seed); out-
  performance is a positive (accumulation) contribution to the `confirmation` sub-score and is
  logged to `signals.csv` (`rs_vs_spy`). SPY is exempt from the liquidity gate.
- **Status:** `IMPLEMENTED` (`scanner.py` + `strategies/wyckoff.py`, tested in
  `tests/test_wyckoff.py` / `tests/test_scanner.py`).

### Volatility Contraction ("the coil")
- **Plain meaning:** Narrowing range/volatility inside a consolidation often precedes the
  expansion move.
- **How it's implemented here:** `wyckoff.score_vol_contraction` compares mean bar range over
  the recent `vol_contraction_window` bars vs the earlier part of the trading range; a tighter
  recent window is a coil. Directionless on its own, so direction comes from range location
  (the single near-support/resistance definition): a coil near support is bullish, near
  resistance bearish. Feeds the `confirmation` sub-score; logged as `vol_contraction`.
- **Status:** `IMPLEMENTED` (`strategies/wyckoff.py`, tested in `tests/test_wyckoff.py`).

### Multi-Timeframe (MTF) Agreement
- **Plain meaning:** A setup confirmed on two timeframes is more reliable than on one.
- **How it's implemented here:** `scanner` reads the other TF's *stored* direction
  (`state.mtf_direction`, no recompute) and passes it into the strategy; Wyckoff
  `score_confirmation` treats it as directional evidence in the `confirmation` sub-score.
  Cold start / not-qualifying-there = neutral (logged `mtf_agree=n/a`).
- **Status:** `IMPLEMENTED` (`state.py` + `scanner.py` + `strategies/wyckoff.py`).

### Liquidity Filter
- **Plain meaning:** Volume-based signals are only trustworthy on liquid names; thin stocks
  produce noise and aren't tradeable at size.
- **How it's implemented here:** 20-day average dollar-volume + min-price gate
  (`universe.passes_liquidity_gate`); illiquid names skipped with a logged reason. This is a
  universe-eligibility filter, so an **absolute** floor is the correct tool here (the
  relative-threshold rule governs *signal* thresholds, not universe gating). SPY is exempt.
- **Status:** `IMPLEMENTED` (`universe.py`, tested in `tests/test_universe.py`).

### Conviction Score / Confirmation Stacking
- **Plain meaning:** Rather than a yes/no call, rank candidates 0–100; independent signals
  agreeing should raise the score.
- **How it's implemented here:** Per-strategy `StrategyResult` scores combined in
  `combiner.combine` (weighted average; direction from the strongest contributor). v1 = one
  strategy, so composite = Wyckoff score. Confirmation *stacking* across strategies awaits a
  second strategy.
- **Status:** `PARTIAL` (`combiner.py`, tested in `tests/test_combiner.py`): aggregation done;
  multi-strategy stacking/correlation still `FUTURE`.

### Signal Correlation Awareness
- **Plain meaning:** Stacked signals only add information if they're *independent*; three
  trend-flavored signals agreeing is one signal counted thrice.
- **How it's implemented here:** Not yet. Designated home is `combiner.py`, using
  per-strategy scores logged in `signals.csv` to measure pairwise correlation later.
- **Status:** `FUTURE`.

### Survivorship Bias
- **Plain meaning:** Testing only on stocks that exist today overstates results (you've
  excluded everything that failed).
- **How it's implemented here:** Live scanning uses today's universe (correct). The
  `src/backtest/` replay engine re-scores *today's* universe over history, so it is
  **knowingly survivorship-biased** — every report prints the caveat, and it's positioned as
  a calibration/iteration tool, not an unbiased verdict. The unbiased path is analysing
  accumulated live `signals.csv` (point-in-time by construction) with the same
  `outcomes`/`metrics` code as it grows (Phase 2).
- **Status:** `PARTIAL` (`src/backtest/`): replay backtester built + caveated; the
  point-in-time (live-`signals.csv`) path is the remaining piece.

### Lookahead Bias
- **Plain meaning:** Accidentally using information not available at decision time, making
  results unrealistically good.
- **How it's implemented here:** Only closed bars reach evaluation — `data._drop_incomplete_last_bar`
  drops a trailing in-session/in-week bar on off-schedule runs (`_last_bar_is_incomplete`,
  conservative 21:00-UTC close cutoff so no tzdata dependency). Scheduled runs are post-close,
  so it's a no-op there. The strategy is also order-independent: evaluating as-of a bar is
  unchanged by appending later bars.
- **Status:** `IMPLEMENTED` (`data.py` fetch guard + the strategy acceptance test
  `test_no_lookahead_evaluation_unaffected_by_future_bars`, SPEC §11/§13).

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
  step that *never invents data* and logs what it touched (`data_quality.clean`). Repairs:
  drop duplicate timestamps, null-OHLC and zero/null-volume bars; forward-fill a single
  isolated missing session. Excludes: unexplained range spike (range >> trailing ATR),
  split-adjustment mismatch with no corporate-action basis, or too few valid bars. For daily,
  completeness is measured against the **NYSE trading calendar** (expected sessions computed in
  `data.py` via `pandas-market-calendars` and passed in, so this module stays pure); weekly
  falls back to completeness vs the bars received. Runs **before** normalization. Pipeline:
  `data → data_quality → features → strategy`.
- **Status:** `IMPLEMENTED` (`data_quality.py` + `data.py`, tested in
  `tests/test_data_quality.py` / `tests/test_data.py`): includes calendar-based missing-bar
  detection (daily). The single-isolated-bar forward-fill uses a flat zero-volume bar.

### Resampling vs. Native Timeframe
- **Plain meaning:** Weekly bars can be aggregated from daily or fetched directly; the choice
  affects history depth and accuracy.
- **How it's implemented here:** Weekly fetched natively (`interval='1wk'`, `auto_adjust=True`)
  in `data.fetch_ohlcv` to avoid resampling artifacts and history-depth math problems; fetch
  window covers `required_history`; results cached per ticker+timeframe+date. At universe
  scale the scanner uses `data.fetch_many` (one threaded `yf.download` batch, OHLCV + splits)
  instead of per-ticker calls, for speed; cache hits skip the network.
- **Status:** `IMPLEMENTED` (`data.py`, pure helpers tested in `tests/test_data.py`; live
  fetch verified manually). SPY is always fetched (gate-exempt).

### State / Dedup
- **Plain meaning:** Re-alerting the same still-valid setup every run is noise; new and
  newly-failed setups are what's actionable.
- **How it's implemented here:** `state.classify_transitions` (pure) maps prior vs.
  current qualifiers to new/continuing/failed; `load_state`/`save_state` persist per-timeframe
  qualifiers (ticker -> score+direction) as JSON; `mtf_direction` reads the other timeframe's
  stored direction for the cross-read. Notifications will fire only on new + failed.
- **Status:** `IMPLEMENTED` (`state.py` + `scanner.py`, tested in `tests/test_state.py` and
  `tests/test_scanner.py`): dedup transitions stamped on signals.csv, MTF cross-read wired.

### Embedded Charts / Dashboard
- **Plain meaning:** Find and inspect candidates in one place rather than exporting to
  another app.
- **How it's implemented here:** `report.render_dashboard` renders a single-file HTML report
  per timeframe (Jinja2 template) with **lazy-loaded** free TradingView embed widgets
  (attribution kept), accumulation/distribution sections ranked by score; plus
  `write_tv_import_file` (secondary `.txt`), `write_index_page` (a small `index.html` landing
  page so the bare Pages URL resolves), and `append_signals` (schema-stable log).
- **Status:** `IMPLEMENTED` (`report.py`, tested in `tests/test_report.py`); wired
  end-to-end by `scanner.py`. CI publishes output to the **gh-pages** branch (GitHub Pages),
  so `main` stays code-only and the dashboard is viewable at
  `https://<user>.github.io/trade-sniffer/latest_<tf>.html`.

### Notification Surface
- **Plain meaning:** Where you get pinged about new setups.
- **How it's implemented here:** `notify.DiscordNotifier` posts a webhook message (NEW/FAILED
  counts, top NEW tickers + report link; condensed on cold start) behind a pluggable
  `Notifier` interface (`make_notifier`); sending is best-effort (failures logged, never
  raised). Telegram and in-channel static chart previews are future options.
- **Status:** `IMPLEMENTED` (Discord — `notify.py` + `scanner.py`, tested in
  `tests/test_notify.py`): scanner builds the NEW/FAILED summary, suppresses empty runs, and
  fires the webhook (cold start = condensed). `FUTURE` (Telegram, chart-image previews).

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
