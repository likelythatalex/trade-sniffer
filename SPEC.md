# SPEC.md — Wyckoff Accumulation/Distribution Scanner

<!--
REVISION NOTE (decisions baked into this draft — review the diff, then approve/flip):
  D1  Weekly bars fetched NATIVELY (yfinance interval="1wk"), not resampled from daily.
  D2  HTML reports are PER-TIMEFRAME: report_<tf>_<date>.html + latest_<tf>.html.
  D3  Exchange prefix resolved from yfinance metadata at fetch time, cached, with an
      optional committed override map; unresolved symbols are skipped-with-reason.
  D4  v1 notifications are DISCORD-ONLY. Telegram deferred (config shape noted as future).
  D5  data_quality.py stays pure; data.py passes corporate-action data in as an argument.
  D6  Per-timeframe parameter overrides are a REQUIREMENT (config + behavior), not a note.
  D7  Cold start: MTF agreement defaults to neutral (no bonus); first run seeds state and
      sends a condensed summary instead of a full NEW flood.
  D8  Embedded chart interval FOLLOWS the run's timeframe (D for daily, W for weekly).
  D9  transition enum pinned to {new, continuing, failed, none}; "invalidated"→failed,
      "still-qualifying"→continuing.
  D10 notify.suppress_empty config key added.
  D11 SPY is always fetched and is exempt from the liquidity gate (needed for RS).
  D12 config.py loading/validation behavior documented (§4.3).
  D13 "self-contained" reworded to "single file, no build step" (the page needs network
      for the TV widget at view time).

REVISION NOTE 2 — per-stock normalization fold-in (docs/wyckoff_methodology.md):
  N1  New pure module features.py sits between data and strategies; produces relative
      features (volume_ratio, volume_pctile, spread_atr, spread_pctile, close_position).
  N2  Pipeline order is fixed: data -> data_quality -> features -> strategy (quality before
      normalization so bad bars don't poison rolling baselines).
  N3  Strategy.evaluate(df, context) now receives the precomputed feature frame via context;
      strategy consumes relative features, not raw OHLCV thresholds.
  N4  Wyckoff signal thresholds become ratios/percentiles ([TUNABLE]/[VERIFY] candidates,
      not final), living under the existing per-timeframe override structure; baseline_window
      added under a new features: block (also per-timeframe).
  N5  History/lookback must cover scoring_window + baseline_window (warmup); validated in
      config.py. This extends the weekly-native-fetch fix (D1).
  N6  signals.csv schema extended with the normalized feature values at the evaluated bar
      (schema version bump / migrate).
  Scope guard: the relative-threshold rule governs Wyckoff *signal* thresholds, NOT the
  liquidity gate (which stays an absolute dollar-volume/price floor by design). No
  regime-aware baselines, no new strategies, no signals beyond the methodology stubs.

REVISION NOTE 3 — refinement pass (final before implementation):
  R1  Liquidity gate stays an intentional absolute universe-eligibility filter (confirmed;
      no change). Relative-threshold rule remains scoped to signal thresholds.
  R2  Range boundaries in v1 = the support/resistance band of §6.1 (UNCHANGED). Climax-
      anchored boundaries are demoted to a FUTURE refinement (§12); climax informs scoring
      *within* the range (range_structure context + volume_behavior), not the boundary.
      Methodology §3 and appendix (Climax, Trading Range) reworded to match this.
  R3  Signal → sub-score mapping pinned as a first-pass (§6.4); within each sub-score the
      signals start at EQUAL weight, every weight a [TUNABLE: calibration seed] tuned
      against signals.csv — not final. Mirrored in methodology §5.
  R4  NaN handling: features.py emits NaN on degenerate bars; the strategy treats NaN as a
      signal that ABSTAINS (zero contribution, no flag); sub-score and composite math must
      never propagate NaN — a ticker always gets a finite score. Propagation test in §11.

REVISION NOTE 4 — final parameterization pass (before implementation):
  P1  [VERIFY] numbers stay as [TUNABLE] calibration seeds (sane starting points; real values
      come from calibrating against signals.csv). No spec change beyond keeping them marked.
  P2  No Demand/No Supply uses the rolling-MEDIAN form ("volume below rolling median"), the
      universe-stable version (consistent with §1). "Previous N bars" kept only as the source's
      original, for traceability. (methodology §2.2/§5)
  P3  "Near support/resistance" is bound to the §6.1 range-position output: near support =
      lower third, near resistance = upper third, fraction = config `range_extreme_fraction`
      (seed 0.33, [TUNABLE]). No separate "near" threshold.
  P4  Spring/upthrust parameterization completed: `spring_lookback` + `spring_snapback_bars`
      added to the per-timeframe wyckoff block (seeds), alongside `spring_wick_pct`.
  P5  Trend-context parameterized for v1: `trend_lookback` + a simple up/down rule (price vs.
      a rising/falling N-period MA, equivalently sign of net change over the lookback). The
      full harmonic rule (volume on impulse vs. correction legs) is [FUTURE] (needs leg
      segmentation), noted in methodology §4 — not a v1 requirement.
-->

## 1. Purpose

Automatically scan a universe of liquid US equities on **Daily** and **Weekly**
timeframes for chart structures showing **high-conviction Wyckoff accumulation or
distribution**, and present qualifying tickers in a **single-file HTML dashboard (no build
step) with embedded interactive TradingView charts**, ranked by conviction, on a recurring
schedule. A link to each new report is pushed to **Discord**.

The system **identifies and flags** candidates only. It does not trade.

## 2. Goals & non-goals

**Goals**
- Reduce hundreds of charts to a short, ranked dashboard of the most structurally
  compelling accumulation/distribution candidates — **find and inspect in one place.**
- Recur automatically per timeframe (Daily after each trading day; Weekly after Friday close).
- Log every evaluation with a transparent score so the scanner itself can be validated over time.

**Non-goals (v1)**
- No order execution, position sizing, or broker integration.
- No intraday timeframes.
- No claim of perfect Wyckoff phase labeling — we detect structural fingerprints and score conviction.
- No ML. Rules-based and explainable first.
- **Wyckoff is the only strategy in v1.** The multi-strategy/correlation machinery is
  *designed for* (see §7) but not built. One strategy, done well, first.
- **Discord-only notifications in v1.** Telegram is deferred; the notify layer is shaped so
  a second channel can be added later without restructuring (see §8.3).

**Position direction:** both **long (accumulation)** and **short (distribution)** setups
are actionable and presented as tradeable candidates, separated and labeled. Distribution
is a short/put candidate, not merely an avoid flag.

## 3. Users & workflow

Single retail trader with a TradingView subscription and an IBKR account.

1. Scanner runs on schedule (GitHub Actions), per timeframe.
2. It builds `output/report_<timeframe>_<date>.html` — a ranked dashboard where each flagged
   ticker shows its conviction score, direction, the reasons it flagged, and an **embedded
   interactive TradingView chart** for immediate inspection.
3. A link/notification is pushed to Discord.
4. User opens the report for that timeframe, scrolls through annotated candidates, and
   inspects charts in place. A `.txt` import file is also produced if they ever want the
   names in their TV account.

> **Why not IBKR or a TradingView import file as primary?** IBKR's watchlist API requires
> Trader Workstation/IB Gateway to be running and logged in, which breaks unattended cloud
> scheduling, and driving its charts from automation is fragile. A plain TV import file
> splits "finding" and "viewing" across two apps. The HTML dashboard puts both in one place,
> keeps unattended scheduling, and needs no API key.

## 4. Inputs

### 4.1 Universe (`universe.txt`)
- One ticker per line, plain text.
- Seeded with liquid large-caps (e.g., S&P 500 / Nasdaq-100 constituents) plus
  user-added custom tickers.
- A **liquidity filter** still runs at scan time (see 6.5) so illiquid customs are skipped
  with a logged reason rather than producing meaningless volume analysis.
- **Exchange prefix is resolved, not stored here.** `universe.txt` carries bare tickers; the
  exchange-prefixed symbol (e.g., `NASDAQ:AAPL`) required by the TV widget and the import
  file is resolved at fetch time (see §4.2 `symbols:` and §5.1). A ticker whose exchange
  cannot be resolved is skipped-with-reason (it cannot produce a valid TV symbol).
- **Survivorship-bias caveat:** `universe.txt` reflects *today's* liquid names. This is
  correct for live scanning, but any future backtest built from it will be biased (it only
  ever contains companies that survived to today). The future backtester (§12) must use a
  point-in-time universe; do not draw backtest conclusions from the live universe file.

### 4.2 Config (`config.yaml`)
```yaml
timeframes: [daily, weekly]

universe_file: universe.txt

data:
  source: yfinance
  daily_lookback_days: 400      # ~18 months; must cover scoring window + baseline warmup (§5A)
  weekly_lookback_weeks: 156    # ~3 years; must cover scoring window + baseline warmup (§5A)
  weekly_fetch: native          # D1: fetch weekly directly (yfinance interval="1wk").
                                #     Do NOT resample weekly from the daily window —
                                #     400 daily days cannot produce 156 weekly bars.
                                #     N5: lookback must also absorb features.baseline_window
                                #     so the earliest scored bar has a full baseline.
  cache_dir: .cache

# D3: how the exchange prefix for TV symbols (e.g. NASDAQ:AAPL) is obtained.
symbols:
  exchange_source: yfinance          # resolve exchange from yfinance metadata at fetch time
  override_map_file: symbol_overrides.csv  # optional ticker,exchange corrections (committed)
  skip_if_unresolved: true           # no exchange -> skip-with-reason (can't build TV symbol)

# N1/N4: per-stock normalization (features.py). All Wyckoff "high/low/narrow/wide" calls
# are relative to each stock's own rolling distribution — never absolute dollars/shares.
features:
  baseline_window:                   # rolling window for volume/spread normalization
    daily: 25                        # [TUNABLE: 20-30 bars] median/percentile, not mean
    weekly: 25                       # [TUNABLE] per-timeframe; tune separately from daily
  # produced features (per ticker/timeframe): volume_ratio, volume_pctile, spread_atr,
  # spread_pctile, close_position. v1 uses a plain rolling window (no regime-aware baseline).

data_quality:
  max_bar_range_atr_mult: 8     # a bar whose range > N x recent ATR is flagged as a spike
  min_valid_bars_pct: 95        # if < this % of expected bars are valid, exclude ticker this run
  drop_zero_volume_bars: true
  verify_split_adjustment: true # D5: corporate-action data is supplied by data.py (see §5.2)

liquidity:
  min_avg_dollar_volume: 20000000   # $20M/day, 20-day average
  min_price: 5.00

# Enabled strategies and their weight in the composite. v1 = wyckoff only.
# Adding a strategy later = drop a file in src/strategies/ + add a line here.
strategies:
  wyckoff:
    enabled: true
    weight: 1.0
  # momentum_regime:        # FUTURE example, not built
  #   enabled: false
  #   weight: 0.0

# D6: Wyckoff parameters. `defaults` apply to every timeframe; `per_timeframe`
# overrides are REQUIRED for weekly so a 60-*week* range isn't silently applied.
# Resolution order at evaluate time: per_timeframe[tf] overrides defaults (key by key).
wyckoff:
  defaults:
    range_lookback: 60          # bars used to define the current trading range
    range_max_width_pct: 25     # range must be reasonably tight to count as consolidation
    min_range_bars: 15          # range must have persisted
    spring_wick_pct: 50         # rejection wick size for spring/upthrust detection
    # N4: relative VSA thresholds over features.py output. CANDIDATES to calibrate against
    # signals.csv — NOT final. Verify against the source before trusting (see methodology
    # doc §1-2, markers [TUNABLE]/[VERIFY]/[CHOICE]).
    high_volume_ratio: 2.0      # [TUNABLE/VERIFY] volume_ratio >= this = "high volume"
    volume_pctile_high: 80      # [TUNABLE] percentile alternative for "high volume"
    narrow_spread_atr: 0.5      # [TUNABLE/VERIFY] spread_atr <= this = "narrow spread"
    # P2: No Demand/Supply uses the rolling-MEDIAN form (volume below rolling median over
    # this window). "previous N bars" is the source's original, kept for traceability only.
    no_demand_supply_median_window: 20  # [TUNABLE] window for the "below rolling median" test
    climax_window: 10           # [TUNABLE/VERIFY] localized window for climax (vol_pctile max)
    climax_reaction_atr: 1.0    # [TUNABLE/VERIFY] "sharp reaction" magnitude, in ATR
    # P3: "near support/resistance" = §6.1 range thirds; this is the fraction of range width.
    range_extreme_fraction: 0.33  # [TUNABLE] lower third = near support, upper third = near resistance
    # P4: spring/upthrust needs more than the wick %; complete the parameterization.
    spring_lookback: 20         # [TUNABLE/VERIFY] bars over which the false-break low/high is judged
    spring_snapback_bars: 3     # [TUNABLE/VERIFY] max bars to close back inside the range
    # P5: simple v1 trend-context measure (the harmonic rule is [FUTURE], see methodology §4).
    trend_lookback: 60          # [TUNABLE] bars for prior up/down-trend (price vs. N-period MA / net change)
  per_timeframe:
    daily: {}                   # uses defaults as-is
    weekly:                     # tune for weekly bars (values below are starting points)
      range_lookback: 26        # ~6 months of weekly bars, not 60 weeks
      min_range_bars: 8
      # any VSA threshold above (high_volume_ratio, narrow_spread_atr, …) may also be
      # overridden here for weekly; left at defaults until calibration says otherwise.
  # internal sub-score weights for THIS strategy's own composite (timeframe-independent)
  sub_weights:
    range_structure: 25
    volume_behavior: 35         # Wyckoff's core: effort vs result
    spring_upthrust: 20
    confirmation: 20            # RS, trend context, volatility contraction, MTF agreement

scoring:
  watchlist_threshold: 70       # 0-100 final composite; >= this gets written out

output:
  dir: output
  report_title: "Wyckoff Scan"
  # D8: embedded TV widget interval follows the run's timeframe.
  embed_chart_interval:
    daily: D
    weekly: W
  theme: dark
  write_tv_import_file: true     # secondary .txt watchlist
  notify:
    enabled: true
    channel: discord             # v1: discord only (telegram deferred — see §8.3)
    webhook_url_env: NOTIFY_WEBHOOK_URL   # from env/secret, never committed
    report_base_url_env: REPORT_BASE_URL  # public/local URL where report is hosted, if any
    suppress_empty: true         # D10: skip the push if a run has no NEW and no FAILED
```

### 4.3 Config loading & validation (`config.py`)
- Loads `config.yaml` into typed dataclasses (no raw dict access downstream).
- Validates on load and **fails fast** (clear error, non-zero exit) before any fetch when:
  required keys are missing, `weekly` is in `timeframes` without `per_timeframe.weekly`
  present, `notify.channel` is anything other than `discord` in v1, `sub_weights` do not sum
  to 100, or a timeframe's configured lookback is smaller than its scoring window +
  `features.baseline_window` (N5 warmup).
- A referenced env var being **set-but-empty is NOT an error**: GitHub Actions turns an unset
  (optional) secret into an empty env var, and the runtime degrades gracefully (notify is
  skipped when the webhook is empty; the report link is omitted when the base URL is empty).
- Exposes a helper that returns the **resolved** Wyckoff params for a given timeframe
  (`defaults` merged with `per_timeframe[tf]`), so strategy code never merges config itself.

## 5. Data layer (`data.py` + `data_quality.py`)

### 5.1 Fetch & cache (`data.py`)
- Fetch OHLCV via `yfinance` per ticker/timeframe.
- **Weekly bars are fetched natively** (`interval="1wk"`, W-FRI anchored), not resampled
  from the daily window — the daily lookback is too short to reconstruct the weekly history.
- Cache to `.cache/` keyed by ticker+timeframe+date to avoid re-pulling within a run.
- **Resolve the exchange prefix** per ticker (§4.2 `symbols:`) and attach it to the frame's
  metadata so report/import-file can build the TV symbol. Unresolved → skip-with-reason.
- **Fetch corporate actions** (splits/dividends) alongside OHLCV and pass them to
  `data_quality.py` so split-adjustment checks stay pure (see §5.2).
- **SPY is always fetched** (for the relative-strength confirmation in §7.1), regardless of
  whether it appears in `universe.txt`, and is **exempt from the liquidity gate**.
- **Warmup (N5):** fetch enough history that the earliest *scored* bar already has a full
  feature baseline — i.e. `lookback ≥ scoring_window + features.baseline_window` per
  timeframe. This is the same history-depth concern that motivated native weekly fetch (D1).
- Per-ticker failures (delisted, empty frame, network) → log and skip, never raise up.
- **No lookahead:** evaluation only uses bars up to and including the last *closed* bar.

### 5.2 Data quality (`data_quality.py`, pure)
Wyckoff lives on volume, so a single bad bar can fake a climax. This step is **transparent
and conservative — detect, log, and repair-only-the-obvious or exclude. Never invent data.**
It is **pure**: all external inputs (including corporate-action data) are passed in as
arguments by `data.py`; it performs no I/O or network calls itself.
- **Detect:** zero/null volume bars; price spikes (bar range > `max_bar_range_atr_mult` ×
  recent ATR); duplicate/missing timestamps; split-adjustment mismatches (e.g., a ~50% gap
  with no corporate-action basis, checked against the passed-in splits data).
- **Repair (only mechanically unambiguous):** drop duplicate timestamps; forward-fill a
  single isolated missing bar at most; re-derive split adjustment if verifiable from the
  passed-in corporate-action data.
- **Exclude:** if valid bars < `min_valid_bars_pct` of expected, or a spike can't be
  explained, skip the ticker this run with a logged reason. A skipped ticker beats a
  fabricated signal. **Exemption:** a large range/gap bar trading on heavy volume
  (≥ `real_move_volume_mult` × trailing median) is a *real* move (earnings/M&A), not a
  glitch, and is kept — glitches don't come with real volume.
- Returns a cleaned frame + a `quality_report` (what was touched) that flows into the log.

## 5A. Feature normalization (`features.py`, pure)

Per the methodology doc, every Wyckoff "high/low volume" or "narrow/wide spread" call is
**relative to each stock's own rolling distribution**, never an absolute dollar or share
figure. `features.py` is a standalone, **strategy-agnostic** pass (reusable by any future
strategy) that turns a cleaned OHLCV frame into a relative-feature frame the strategy
consumes. It is pure (no I/O).

- **Pipeline position (N2, fixed order):** `data → data_quality → features → strategy`.
  Quality runs *before* normalization so a bad bar can't poison the rolling baseline (a
  fabricated volume spike would otherwise distort the median/percentile for many bars).
- **Features produced** (rolling over `features.baseline_window`, per ticker/timeframe):
  - `volume_ratio` — bar volume ÷ rolling **median** volume (median, not mean — volume is
    right-skewed; one climax bar inflates a mean).
  - `volume_pctile` — percentile rank of bar volume within the window.
  - `spread_atr` — bar range (high−low) ÷ rolling ATR (the relative-spread measure).
  - `spread_pctile` — percentile rank of bar range within the window.
  - `close_position` — (close − low) ÷ (high − low), where the close landed in the bar.
- **Baseline window** is a calibration parameter (`features.baseline_window`, [TUNABLE:
  20–30 bars]); v1 uses a plain medium rolling window. A regime-aware baseline (e.g.
  post-earnings ATR inflation) is **explicitly out of scope** for v1.
- **Degenerate-bar guard:** define behavior when a bar has zero range (`high == low`, e.g.
  a halt/limit day) or the rolling ATR/median is zero — the affected feature is emitted as
  NaN and the strategy treats NaN as "no signal" for that bar (it is not coerced to a
  misleading 0 or 1). Bars with insufficient preceding history (< `baseline_window`) have
  undefined features and are not scored (covered by the §5.1 warmup rule). NaN must
  **degrade gracefully**: the dependent signal abstains and the sub-score/composite never
  propagate NaN (see §6.4 NaN-safe aggregation).
- **Scope of normalization:** runs on each candidate ticker. SPY (a reference series for RS,
  §7.1) does not require the full feature frame.
- Returns a feature frame aligned to the cleaned OHLCV index; the specific VSA *signal*
  definitions that consume these features live in `docs/wyckoff_methodology.md` (kept as
  verifiable `[VERIFY]`/`[TUNABLE]` stubs — numeric thresholds are candidates, not final).
  The domain-concept glossary with implementation-status tags is `docs/appendix.md`; its
  *Per-Stock Normalization* entry (and the §D calibration-parameter table) must be updated
  when this module lands.

## 6. Strategy architecture (`strategies/`)

The analysis engine is built around **one small abstraction** so conviction can later be
raised/lowered by stacking independent strategies — without an engine rewrite.

- **`strategies/base.py`** defines:
  - `StrategyResult` (`@dataclass`): `direction` (`accumulation`/`distribution`/`none`),
    `score` (0–100, normalized), `sub_scores` (dict), `reasons` (list of plain-English
    tags for the dashboard), `metadata`.
  - `Strategy` interface: a single method `evaluate(df, context) -> StrategyResult`, pure
    (no I/O). `df` is the cleaned raw OHLCV frame; `context` carries the **precomputed
    feature frame (§5A)** for this ticker/timeframe (N3), **the resolved per-timeframe
    params (§4.3)**, timeframe, prior-run state (for MTF), and config. Strategies read
    relative features from `context`; they never re-derive normalization or apply absolute
    volume/spread thresholds.
- **`strategies/registry.py`** maps strategy name → class, built from the `strategies:`
  block in config. `scanner.py` never hardcodes which strategies exist.
- **`strategies/wyckoff.py`** is the only strategy in v1 (§6.1–6.4 below are its internals).
- **`combiner.py`** aggregates enabled strategies' `StrategyResult`s into the final
  composite per ticker/timeframe, using `strategies.*.weight`. v1 has one strategy so the
  composite equals Wyckoff's score. **This is the single, designated home for future
  correlation-awareness** (down-weighting strategies whose scores are historically
  correlated, measured from `signals.csv`). Keep aggregation logic here only.

> **Why this matters now:** it costs almost nothing (one dataclass + one interface) and
> means adding a momentum/RS/volatility strategy later is "new file + config line," not a
> refactor. Confirmation stacking and correlation-awareness both land in `combiner.py`.

## 6A. Wyckoff strategy internals (`strategies/wyckoff.py`)

All functions are pure. The strategy operates on the **precomputed relative-feature frame
(§5A)** plus the raw OHLCV frame, reads its **resolved per-timeframe params** from `context`
(never merges config itself), and combines its sub-scores using `wyckoff.sub_weights` into
its own 0–100 score before the combiner sees it. All "high/low/narrow/wide" tests use the
relative features with thresholds from config (e.g. `high_volume_ratio`, `narrow_spread_atr`,
`volume_pctile_high`) — no absolute thresholds. The precise VSA signal definitions (effort
vs. result, No Demand/No Supply, climax, spring/upthrust) are specified in
`docs/wyckoff_methodology.md`; mapping each signal to a sub-score and its relative weight is
an open calibration item there, not fixed here.

### 6.1 Trading range detection
- Identify the most recent consolidation: a window (`range_lookback`) where price
  oscillates between a support and resistance band.
- Reject if range too wide (`range_max_width_pct`) or too short (`min_range_bars`) —
  trending or noisy charts aren't accumulation/distribution setups.
- Output: range high, range low, width, duration, and where price sits within the range.
  The **range-position output is the single definition of "near support/resistance"** used
  everywhere downstream (No Demand/Supply, volume-at-extremes): price within the bottom
  `range_extreme_fraction` of the range = **near support** (accumulation bias); within the
  top `range_extreme_fraction` = **near resistance** (distribution bias). No separate "near"
  threshold exists. (`range_extreme_fraction` seed 0.33 = thirds, `[TUNABLE]`.)

### 6.2 Volume behavior — the heart of Wyckoff
This carries the most weight (35). Effort (volume) vs. result (price movement):
- **Accumulation signs:** volume dry-up on declines into support; volume expansion on
  rallies off support; down-bars on low volume near the lows.
- **Distribution signs:** volume expansion on declines from resistance; volume dry-up on
  rallies into resistance; up-bars on low volume near the highs.
- Compare recent volume distribution at range extremes vs. the range average.
- All of the above are expressed over §5A relative features (`volume_ratio`,
  `volume_pctile`, `spread_atr`, `close_position`) with config thresholds — never raw
  share/dollar levels. Specific VSA markers are stubbed in `docs/wyckoff_methodology.md` §2.
- Output: a directional volume score (positive = accumulation, negative = distribution)
  and a magnitude.

### 6.3 Spring / upthrust detection
- **Spring:** within `spring_lookback` bars, price briefly breaks below range support then
  closes back inside within `spring_snapback_bars`, on a rejection wick (`spring_wick_pct`)
  — a bullish accumulation tell. (All three are per-timeframe `[TUNABLE]` seeds;
  `spring_wick_pct` alone is insufficient to specify the pattern.)
- **Upthrust:** mirror image above resistance — bearish distribution tell.
- Bonus weight if the false-break happened on a volume characteristic consistent with
  6.2 (e.g., spring on declining volume, or recovery on rising volume).

### 6.4 Phase bias & strategy score

**Signal → sub-score mapping (first-pass; weights are calibration seeds, not final).** Each
signal feeds exactly one sub-score. Within a sub-score, the listed signals start at *equal*
weight, every weight marked `[TUNABLE: calibration seed]` to be tuned against `signals.csv`
once data accumulates — this is a starting structure that unblocks `wyckoff.py`, not a final
allocation. (Mirrored in `docs/wyckoff_methodology.md` §5.)

| Sub-score (config weight) | Signals it aggregates |
|---|---|
| `volume_behavior` (35) | Effort-vs-Result (§6.2 / methodology §2.1), No Demand / No Supply (§2.2), climax volume characterization (§2.3) |
| `spring_upthrust` (20) | spring / upthrust detection + volume corroboration (§6.3 / methodology §2.4) |
| `range_structure` (25) | range validity/quality (§6.1) + climax as *context* within the range (not as the boundary) |
| `confirmation` (20) | RS-vs-SPY, volatility contraction, MTF agreement, trend context (§7) |

- **NaN-safe aggregation:** a signal whose feature inputs are NaN (a §5A degenerate bar)
  contributes **zero and raises no flag** ("signal abstained"). Sub-score and composite math
  must never propagate NaN — every evaluated ticker receives a finite score, never an
  unscorable result.
- Combine sub-scores using `wyckoff.sub_weights` into a 0–100 score.
- Assign a **direction**: `accumulation` (long candidate) or `distribution` (short candidate),
  or `none`.
- Report the direction with the stronger evidence per ticker/timeframe. Both directions are
  actionable downstream and shown as tradeable setups.
- Emit a `StrategyResult` (score, direction, sub_scores, reason tags, metadata).

### 6.5 Liquidity gate (`universe.py`, inline at scan time)
- Compute 20-day average dollar volume; drop below `min_avg_dollar_volume` or
  `min_price`. Logged as skipped-with-reason.
- **SPY is exempt** from this gate (it is a reference series for §7.1, not a candidate).

## 7. Secondary confirmation — "higher conviction, kept simple"

These feed the Wyckoff strategy's own `confirmation` sub-score (weight 20 in
`wyckoff.sub_weights`). They are *within-strategy* corroboration, distinct from the
*cross-strategy* stacking that `combiner.py` will eventually do. All cheap, rules-based.
Kept short on purpose:

1. **Relative strength vs. SPY.** For accumulation, is the stock holding up better than the
   index on down/flat tape? Mirror for distribution. Strong "smart money positioned" tell.
   SPY is always fetched (§5.1) so this is available every run.
2. **Volatility contraction.** Bollinger Band width or ATR contracting inside the range
   ("the coil") often precedes the markup/markdown. Cheap, high signal.
3. **Multi-timeframe agreement (cross-read, not recompute).** A ticker flagged the same
   direction on **both** Daily and Weekly gets a bonus — the single biggest free win. Since
   D and W run on different schedules, the running timeframe reads the **most recent stored
   result** for the other timeframe from `state.py` (e.g., a Daily run reads the last
   Weekly signal). It never recomputes the other timeframe inline. **Cold start:** if no
   stored result exists for the other timeframe, MTF agreement is **neutral (no bonus, no
   penalty)** and the cards/log show "MTF: n/a".
4. **Trend context.** Accumulation is most meaningful after a prior downtrend; distribution
   after a prior uptrend. Penalize setups lacking the preceding move. **v1 measure (simple,
   parameterized):** over `trend_lookback` bars, classify the prior trend by price vs. a
   rising/falling N-period MA (equivalently, the sign of net change over the lookback).
   The fuller "harmonic rule" (volume behavior on impulse vs. correction legs) needs
   impulse/correction leg segmentation and is `[FUTURE]`, not v1 — see methodology §4.

> Deliberately excluded for v1: order-flow/footprint, options flow, fundamentals. These add
> conviction but also cost/complexity — future phases (and several would arrive as their own
> *strategies* via §6, not as more confirmation factors here).

## 8. Output (`report.py`)

### 8.1 HTML dashboard (primary)
- Render `output/report_<timeframe>_<YYYY-MM-DD>.html` from a Jinja2 template (one report
  per timeframe per run), plus copy/symlink to `output/latest_<timeframe>.html`. Daily and
  Weekly therefore never clobber each other's files.
- **Ranked by composite score, descending,** within each direction. Accumulation and
  distribution candidates in separate, clearly labeled sections.
- Each candidate card shows:
  - Ticker, exchange-prefixed symbol, direction, composite score (0–100).
  - **Score breakdown** (range / volume / spring / confirmation sub-scores) so you can see
    *why* it flagged at a glance.
  - Plain-English reason tags (e.g., "volume dry-up at support", "spring on Tue",
    "outperforming SPY", "flagged on both D + W").
  - An **annotated interactive chart** for the selected candidate, rendered with TradingView's
    open-source **Lightweight Charts™** from OHLCV embedded in the page. Annotations: the
    range high/low band and a spring/upthrust marker. (Superseded the v1 display-only embed
    widget, per §12 — the embed couldn't take programmatic annotations.)
- **Layout:** a ranked candidate list (accumulation/distribution) + **one shared chart** that
  loads the clicked candidate, so you cycle through setups without N heavy charts.
- **Single file, no build step:** OHLCV + annotations are embedded as JSON, so there's **no
  view-time data fetch**; the page loads the Lightweight Charts library from a pinned CDN
  script. **No API key.** An "open in TradingView ↗" link per candidate preserves access to
  TV's full toolset for deep manual analysis.
- **Keep TradingView attribution visible** (the open-source library's license requires it).
- Top-of-page run summary: timeframe, counts (scanned / flagged / skipped / errored), run
  timestamp, and a collapsible "N skipped — why" list of excluded tickers + reasons.

### 8.2 TradingView import file (secondary, near-free)
- Also write `output/watchlist_daily.txt` / `watchlist_weekly.txt`, one exchange-prefixed
  symbol per line (e.g., `NASDAQ:AAPL`), optional `###ACCUMULATION` / `###DISTRIBUTION`
  group markers. For the occasions you want the names inside your TV account.

### 8.3 Notification (Discord) — newly-qualifying + invalidations only
v1 sends to **Discord only** via webhook. `notify.py` is written so a second channel
(Telegram) can be added later behind the same interface without touching callers.
Notification fires on **state transitions**, not the full list, to avoid noise (`state.py`
holds the prior run's qualifying set per timeframe):
- **NEW** — ticker qualifies (≥ threshold) that was not qualifying on the prior run of that
  timeframe. Every timeframe reports each newly-qualifying ticker at least once.
- **FAILED** (a.k.a. invalidated) — a previously-qualifying setup that has now dropped below
  threshold or whose structure broke (e.g., range violated, spring failed). This transition
  *is* notified, since a failing setup is actionable (exit/avoid).
- **Still-qualifying, unchanged** (transition `continuing`) — not re-notified.
- **Cold start (no prior state for a timeframe):** seed state and send a single **condensed
  summary** (counts + top NEW tickers), not a full per-ticker NEW flood.
- Message contents: timeframe, counts of NEW and FAILED, the top NEW tickers with scores +
  direction, and a report link (`REPORT_BASE_URL` + filename if hosted; else summary + local
  path). Webhook URL and base URL from env/secrets, never committed.
- Suppress entirely if a run has no NEW and no FAILED transitions
  (`notify.suppress_empty`, default true).

### 8.4 Signal log (`output/signals.csv`)
- Append every evaluated ticker (even sub-threshold) per run. Schema:
  `run_ts, ticker, timeframe, direction, composite_score, wyckoff_score, range_score,
  volume_score, spring_score, confirmation_score, rs_vs_spy, vol_contraction, mtf_agree,
  trend_context, data_quality_flag, feat_volume_ratio, feat_volume_pctile, feat_spread_atr,
  feat_spread_pctile, feat_close_position, made_watchlist, transition` where `transition` ∈
  {`new`, `continuing`, `failed`, none}. ("Invalidated" maps to `failed`;
  "still-qualifying" maps to `continuing`.) `mtf_agree` is empty/`n/a` on cold start.
- **N6:** the `feat_*` columns log the §5A normalized features **at the evaluated (last
  closed) bar**, so calibration can later relate thresholds to outcomes. Adding these is a
  schema change — bump the schema version and migrate existing `signals.csv` (don't break
  the log; per the CLAUDE.md definition-of-done).
- Per-strategy columns are namespaced so adding strategies extends (not breaks) the schema;
  `composite_score` is the combiner output, `wyckoff_score` the strategy's own; `feat_*`
  columns are strategy-agnostic (produced by `features.py`).
- This is the audit trail, the dedup source, and the dataset for future backtesting AND
  cross-strategy correlation analysis (§7 / §6 combiner).

### 8.5 Agent reviewer (`review.py`) — objective due diligence, precomputed

An optional, proactive reviewer that gives an objective second opinion on flagged setups —
distinct from a chat window: it fires automatically (no human prompt), applies the *same
skeptical rubric* to every candidate, and emits a structured verdict. It reviews the *signal*
(score, sub-scores, reason tags, recent price action), not the qualitative chart vision, and
**never gives trading advice** (analyst notes only — the tool flags candidates, never trades).

- **Precomputed at scan time, not live.** The dashboard is a static gh-pages file, so the
  scheduled run calls the LLM and bakes the review text into the card. No view-time backend;
  the API key is a GitHub Secret, never in the page.
- **Strategy-agnostic.** Consumes the normalized card (the `StrategyResult` contract), so
  future strategies are reviewed with no changes. v1 reasons over the evidence in the prompt;
  tool-using (deeper) agency is a future upgrade.
- **Cost controls (public repo):** off by default (`review.enabled`); reviews **NEW
  transitions only** (continuing setups reuse the cache); a hard **per-run cap**
  (`max_reviews_per_run`); a cheap model (`review.model`, default Haiku); bounded output
  (`max_tokens`) and a compact prompt; reviews cached by `timeframe:ticker` in `reviews.json`
  (carried on gh-pages) so same-day re-runs and continuing setups never re-spend.
- **Pluggable + fail-soft** (mirrors `notify.py`): the `Reviewer` interface allows another
  provider later; no key or a failed call simply omits the review and the run continues.
- **Provider:** v1 is Anthropic via the REST Messages API (`requests`, no SDK dependency).
- Output: a `Verdict: aligned|mixed|skeptical` line + a short assessment + a concerns list,
  rendered as text (never HTML-injected — it's model output on a public page).

## 8A. Trade layer — planner + local journal (planned; not yet built)

**Framing:** *signal* and *trade* are different jobs. The strategy says **what / which
direction**; the trade layer says **where to enter, where to exit, how much, and how it
went**. It is a **second seam parallel to `Strategy`** — signal-agnostic by construction, so
any future strategy that emits a direction + structural levels gets trade plans for free.
**Still never trades** — this is planning + journaling only (no broker, ever).

**Interface touch-point:** `StrategyResult` gains a typed `levels` (range high/low, entry
reference, invalidation/stop reference, target reference). Strategies populate what they know;
the planner reads `levels`, never Wyckoff internals — that's what keeps it strategy-agnostic.

### 8A.1 Trade planner (`trade_plan.py`, pure, public)

Consumes a `StrategyResult` → a `TradePlan` dataclass (entry, stop, target, reward:risk,
size, management rules). All numbers are `[TUNABLE]` seeds in a flat `trade_plan:` config
block (per-timeframe overrides deferred — sizing is account-level and the buffer/playbook
start global; add the split when calibration shows daily ≠ weekly). The planner is **pure**
on `(direction, levels, config)` and **abstains** (`None`) on a degenerate setup rather than
raising — the same fail-soft contract as the rest of the pipeline.

- **Entry:** confirmation break of the range edge in the signal's direction.
- **Stop:** a **selectable method** (`stop_method`) — the reward:risk lever. `capped`
  (default) takes the structural invalidation (spring low / upthrust high, else the range
  edge) + buffer but pulls it in to at most `max_stop_pct` from entry, for a healthier R:R
  (the trade-off: a capped stop sits inside structure, so it's more exposed to noise);
  `structural` keeps the full invalidation + buffer (wide stop, R:R ~1); `atr` sizes the stop
  off `Levels.atr`. Selectable so a future sweep can *tune* the policy. (Entry/target stay
  single-method for now — they parameterize the same way when a 2nd method is wanted; YAGNI.)
- **Target:** measured move (range height projected from the break).
- **Sizing:** account-risk % — `size = (account_notional × risk_pct) / stop_distance`.
  Defaults **1% on a $100k notional** (notional only scales the displayed size; nothing trades).
- **Management** (a written playbook baked into the plan, not executed): breakeven at +1R;
  scale 50% at target; trail the remainder by N×ATR.
- **Display:** rendered on each dashboard card; entry/stop/target drawn as `createPriceLine`
  on the existing Lightweight Charts chart. Public — derived from public price + levels, no
  personal data.

### 8A.2 Local trade journal (`journal.py` + CLI) — PRIVATE, local-only

The repo and gh-pages are **public**, so intended trades must never touch either. The journal
is therefore **gitignored, never committed, never published**, and runs **only locally /
manually** — never in CI (CI output is public by definition). This is a clean public-scanner /
private-journal split.

- **Record:** `python -m src.journal add …` (or edit `journal.csv`): ticker, timeframe,
  direction, entry, stop, target, size, opened date, source signal; status open/closed, exit,
  exit date.
- **Auto-outcome:** a **path-dependent** evaluator (`trade_outcome.py`, pure) walks the
  forward price path bar-by-bar — which level hit first (stop vs target), realized R, MFE/MAE
  — surfaced by `journal report`. This is distinct from `backtest/outcomes.py` (close-to-close
  forward returns for the score's IC); a trade plan is path-dependent, so it's its own module,
  **shared** with the future policy-sweep simulator (Tier 3). Outcomes are *derived* (recomputed
  from prices), never stored in `journal.csv` — the journal stays pure user input. The journal
  is the *real-trade* dataset alongside `signals.csv`.
- **Post-trade agent review (private):** reuse the `Reviewer` ABC (§8.5) with a *reflection*
  system prompt — "how did this closed trade go, what worked / didn't, the lesson." Sends
  trade details to the Anthropic API from your machine (a private call, but it does leave the
  machine — acknowledged). **Symmetry:** pre-trade reviewer = public, on signals; post-trade
  reviewer = private, on closed trades. Same interface, different prompt.
- **Privacy enforcement:** `.gitignore` covers `journal.csv`, journal outputs, and post-trade
  reviews; the published report never renders the journal.

### 8A.3 Disclaimer

A short, informal disclaimer in the README **and** the dashboard footer: educational/personal
project, **not financial advice**, flags candidates only (never trades), no warranty, signals
can be wrong, do your own research.

## 9. Scheduling (`.github/workflows/scan.yml`)

- **Daily scan:** cron after US market close (e.g., 22:00 UTC weekdays), accounting for
  the fact GitHub cron is UTC and not guaranteed to the minute.
- **Weekly scan:** cron Friday after close / Saturday.
- Steps: checkout → setup Python → install → run scanner → write report + import file +
  signals.csv → commit `output/` back to the repo (preserves history) → push
  Discord notification.
- **Viewing the report:** simplest option is to open the committed `latest_<timeframe>.html`
  from the repo. For a clickable mobile link in the notification, optionally publish
  `output/` via **GitHub Pages** (free) and set `REPORT_BASE_URL` to the Pages URL. Note: if
  published publicly, that satisfies the TV widget's "public, not paywalled" license
  condition; if you'd rather keep it private, skip Pages and open the file locally.
- Secrets (notify webhook URL, report base URL) via GitHub Secrets.

## 9A. State & dedup (`state.py`)

- Reads the prior run's results (from `signals.csv` and/or a small `state.json`) to support
  two things: **notification dedup** (§8.3) and the **multi-timeframe cross-read** (§7.3).
- Tracks, per timeframe, the set of currently-qualifying tickers and their last score so the
  next run can classify each as new / continuing / failed.
- Stores the latest result per timeframe so the *other* timeframe's run can read it without
  recomputation. When absent (cold start), the cross-read returns "n/a" and MTF is neutral.
- Pure-ish: file reads/writes isolated to this module; the transition logic itself is a pure
  function (prior set + current set → transitions) so it is unit-testable.

## 10. Error handling & reliability

- Per-ticker try/except with structured logging; run summary reports counts
  (scanned / flagged / skipped-illiquid / skipped-data-quality / errored).
- If data source returns nothing for a large fraction of the universe, treat as a source
  outage: abort and alert rather than writing an empty/misleading report.
- Idempotent: same-day re-run overwrites cleanly, no dupes.

## 11. Testing

- Unit tests for `strategies/wyckoff.py` using small hand-built OHLCV fixtures with known
  expected labels (textbook accumulation, textbook distribution, a trending chart that
  should score ~0, a spring, an upthrust).
- Unit tests for `data_quality.py` (zero-volume bar, price spike, dup timestamp, a frame
  that should be excluded) and for `state.py` transition logic (new / continuing / failed).
- Unit tests for `features.py`: known fixture → expected `volume_ratio`/`spread_atr`/
  `close_position`; right-skewed volume confirms median (not mean) behavior; degenerate bars
  (`high == low`, zero ATR) yield NaN, not a coerced value; bars before `baseline_window`
  are unscored.
- **NaN-propagation test:** a ticker whose evaluated bar has one or more NaN features still
  produces a finite composite score (the affected signal abstains, contributing zero); assert
  the result is never NaN and the ticker is never dropped as "unscorable" for this reason.
- Unit test that `config.py` resolves per-timeframe params correctly (weekly overrides
  applied over defaults) and rejects an invalid config (e.g., missing `per_timeframe.weekly`,
  non-discord channel, sub_weights not summing to 100).
- Golden-file test for the import-file format and the report's data model (card dicts), so
  output-format changes are caught.
- A smoke test running the full scanner on ~10 tickers end to end.

## 12. Future phases (explicitly out of scope now, architected for)

> **Prioritization, sequencing, and status live in `ROADMAP.md`** (the single source of
> truth for the backlog), as do small near-term operational todos that don't need a design
> entry. This section is the *design rationale* for the larger future items; ROADMAP links
> back here rather than repeating the "why".

- **Additional strategies** (momentum regime, relative strength, volatility) implementing
  the §6 `Strategy` interface — each a new file in `strategies/` + a config line.
- **Telegram notification channel** behind the §8.3 `notify.py` interface (adds
  bot-token/chat-id config; v1 is Discord-only).
- **Confirmation stacking**: combiner raises conviction when independent strategies agree.
- **Correlation-awareness in `combiner.py`**: measure pairwise correlation of strategy
  scores from `signals.csv`; down-weight clusters that are really one signal, so agreement
  means something. (This is *why* per-strategy scores are logged separately now.)
- **Backtesting harness**: replay `signals.csv` forward N bars to test whether high scores
  preceded markup/markdown. Must use a point-in-time universe to avoid survivorship bias
  (§4.1). The §8.4 logging is designed to enable this.
- **Regime-aware feature baselines** (e.g. resetting the volatility baseline after an
  earnings-driven expansion so "narrow spread" stays meaningful). v1 deliberately uses a
  plain rolling window; this is acknowledged but out of scope.
- Paid data source swap (Polygon) if intraday or higher data quality is later needed.
- **Climax-anchored range boundaries.** v1 defines the trading range as a support/resistance
  band (§6.1). A refinement is to anchor the boundaries off climax-driven automatic
  rally/reaction levels (methodology §2.3/§3); deferred because it adds a detection step and
  needs validation. In v1, climax only informs *scoring within* the range (§6.4 mapping),
  not the boundary.
- **Annotated interactive charts via TradingView Lightweight Charts™.** The free TV *embed
  widget* (v1) is display-only — it cannot take programmatic drawings or custom indicators,
  so critical points can't be highlighted on it. The path to annotations (range high/low
  lines, spring/upthrust markers, score labels) is to render charts ourselves with
  TradingView's open-source **Lightweight Charts** JS library, fed by the OHLCV we already
  fetch. Trade-off: we lose TV's full drawing toolset but gain full annotation control while
  staying interactive and single-file. This would supersede the v1 embed widget for the
  dashboard. (Pine Script is not injectable into an embed; it's a separate, manual path.)
  Complementary to — not a replacement for — the Discord static chart preview below:
  Lightweight Charts is the *interactive dashboard* surface, the PNG snapshot is the
  *glanceable notification* image. Both remain in scope.
- **Crypto mode (e.g. `BTC-USD`, `ETH-USD`).** Wyckoff/VSA applies to crypto and yfinance
  serves it, but it needs a separate profile rather than a drop-in: 24/7 trading (no market
  calendar / post-close scheduling assumption), a crypto RS benchmark instead of SPY,
  liquidity gating in crypto terms (not the $-volume / $5-price floor), crypto TV symbol
  prefixes (`BINANCE:` / `COINBASE:`), and no corporate actions. Belongs to the **same future
  phase as the multi-strategy expansion (§6/§7)**: strategies are applied per asset class
  *where applicable*, selected via the registry + config.
- **Discord static chart preview (v1.1 nicety)**: render a PNG snapshot per top ticker
  (headless chart render) and attach it to the notification, so the channel shows a
  glanceable preview before clicking through to the interactive dashboard. Discord cannot
  host the interactive widget itself — it stays a notification surface, the HTML dashboard
  stays the inspection surface.
- IBKR integration as an *optional* extra output for users who keep TWS running.

## 13. Acceptance criteria (v1)

- [ ] Runs end-to-end on the sample universe on Daily and Weekly with no crashes.
- [ ] Weekly data is fetched natively (not resampled from the daily window) and provides the
      configured `weekly_lookback_weeks` of history.
- [ ] Produces a single-file HTML dashboard per timeframe, ranked by score, with a working
      embedded TradingView chart per flagged ticker (interval matching the timeframe) and
      visible TV attribution. Daily and Weekly reports do not overwrite each other.
- [ ] Both accumulation (long) and distribution (short) setups are shown, separated/labeled.
- [ ] Data-quality step excludes/flags bad bars and logs what it touched; never invents data;
      stays pure (corporate-action data passed in by `data.py`).
- [ ] `features.py` produces the relative-feature frame (volume_ratio, volume_pctile,
      spread_atr, spread_pctile, close_position) on a per-stock rolling baseline; the
      pipeline runs strictly `data → data_quality → features → strategy`.
- [ ] The strategy consumes relative features via `context` and applies no absolute
      volume/spread thresholds; all such thresholds are config ratios/percentiles.
- [ ] Each signal maps to one sub-score per the §6.4 first-pass mapping (weights are config
      calibration seeds); NaN features abstain and never propagate into the composite.
- [ ] Per-timeframe lookback covers scoring window + baseline warmup (validated in config).
- [ ] `signals.csv` includes the `feat_*` columns at the evaluated bar (schema migrated).
- [ ] Writes the secondary TV import file that imports cleanly.
- [ ] Notification fires only on NEW and FAILED transitions; still-qualifying not re-sent;
      Discord-only; suppressed when empty (configurable); cold start sends a condensed summary.
- [ ] Every run appends a complete, schema-correct row per ticker to `signals.csv`, with
      per-strategy columns and a `transition` value in {new, continuing, failed, none}.
- [ ] All thresholds, weights, and enabled strategies are driven by `config.yaml`, and
      Wyckoff params are resolved per timeframe (weekly overrides applied over defaults).
- [ ] MTF agreement uses a cross-read of the other timeframe's stored result (no recompute),
      defaults to neutral on cold start, and is visible in the log and on the cards.
- [ ] Wyckoff implements the `Strategy` interface; `combiner.py` produces the composite
      (trivially, for one strategy) — adding a second strategy needs no engine change.
- [ ] No lookahead: a test confirms evaluation on bar N is unchanged by appending bar N+1.
- [ ] Scheduled GitHub Action runs unattended and commits output + sends notification.
