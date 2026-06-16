# SPEC.md — Wyckoff Accumulation/Distribution Scanner

## 1. Purpose

Automatically scan a universe of liquid US equities on **Daily** and **Weekly**
timeframes for chart structures showing **high-conviction Wyckoff accumulation or
distribution**, and present qualifying tickers in a **self-contained HTML dashboard with
embedded interactive TradingView charts**, ranked by conviction, on a recurring schedule.
A link to each new report is pushed to **Discord/Telegram**.

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

**Position direction:** both **long (accumulation)** and **short (distribution)** setups
are actionable and presented as tradeable candidates, separated and labeled. Distribution
is a short/put candidate, not merely an avoid flag.

## 3. Users & workflow

Single retail trader with a TradingView subscription and an IBKR account.

1. Scanner runs on schedule (GitHub Actions).
2. It builds `output/report_<date>.html` — a ranked dashboard where each flagged ticker
   shows its conviction score, direction, the reasons it flagged, and an **embedded
   interactive TradingView chart** for immediate inspection.
3. A link/notification is pushed to Discord/Telegram.
4. User opens the one report, scrolls through annotated candidates, and inspects charts
   in place. A `.txt` import file is also produced if they ever want the names in their
   TV account.

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
  daily_lookback_days: 400      # ~18 months, enough for range + trend context
  weekly_lookback_weeks: 156    # ~3 years
  cache_dir: .cache

data_quality:
  max_bar_range_atr_mult: 8     # a bar whose range > N x recent ATR is flagged as a spike
  min_valid_bars_pct: 95        # if < this % of expected bars are valid, exclude ticker this run
  drop_zero_volume_bars: true
  verify_split_adjustment: true

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

wyckoff:
  range_lookback: 60            # bars used to define the current trading range
  range_max_width_pct: 25       # range must be reasonably tight to count as consolidation
  min_range_bars: 15            # range must have persisted
  spring_wick_pct: 50           # rejection wick size for spring/upthrust detection
  # internal sub-score weights for THIS strategy's own composite
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
  embed_chart_interval: D        # default interval shown in embedded TV widget
  theme: dark
  write_tv_import_file: true     # secondary .txt watchlist
  notify:
    enabled: true
    channel: discord             # discord | telegram
    webhook_url_env: NOTIFY_WEBHOOK_URL   # from env/secret, never committed
    report_base_url_env: REPORT_BASE_URL  # public/local URL where report is hosted, if any
```

## 5. Data layer (`data.py` + `data_quality.py`)

### 5.1 Fetch & cache (`data.py`)
- Fetch OHLCV via `yfinance` per ticker/timeframe.
- Cache to `.cache/` keyed by ticker+timeframe+date to avoid re-pulling within a run.
- Weekly bars: resample from daily (W-FRI anchored) for consistency.
- Per-ticker failures (delisted, empty frame, network) → log and skip, never raise up.
- **No lookahead:** evaluation only uses bars up to and including the last *closed* bar.

### 5.2 Data quality (`data_quality.py`, pure)
Wyckoff lives on volume, so a single bad bar can fake a climax. This step is **transparent
and conservative — detect, log, and repair-only-the-obvious or exclude. Never invent data.**
- **Detect:** zero/null volume bars; price spikes (bar range > `max_bar_range_atr_mult` ×
  recent ATR); duplicate/missing timestamps; split-adjustment mismatches (e.g., a ~50% gap
  with no corporate-action basis).
- **Repair (only mechanically unambiguous):** drop duplicate timestamps; forward-fill a
  single isolated missing bar at most; re-derive split adjustment if verifiable.
- **Exclude:** if valid bars < `min_valid_bars_pct` of expected, or a spike can't be
  explained, skip the ticker this run with a logged reason. A skipped ticker beats a
  fabricated signal.
- Returns a cleaned frame + a `quality_report` (what was touched) that flows into the log.

## 6. Strategy architecture (`strategies/`)

The analysis engine is built around **one small abstraction** so conviction can later be
raised/lowered by stacking independent strategies — without an engine rewrite.

- **`strategies/base.py`** defines:
  - `StrategyResult` (`@dataclass`): `direction` (`accumulation`/`distribution`/`none`),
    `score` (0–100, normalized), `sub_scores` (dict), `reasons` (list of plain-English
    tags for the dashboard), `metadata`.
  - `Strategy` interface: a single method `evaluate(df, context) -> StrategyResult`, pure
    (no I/O). `context` carries timeframe, prior-run state (for MTF), and config.
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

All functions are pure. The strategy combines its sub-scores using `wyckoff.sub_weights`
into its own 0–100 score before the combiner sees it.

### 6.1 Trading range detection
- Identify the most recent consolidation: a window (`range_lookback`) where price
  oscillates between a support and resistance band.
- Reject if range too wide (`range_max_width_pct`) or too short (`min_range_bars`) —
  trending or noisy charts aren't accumulation/distribution setups.
- Output: range high, range low, width, duration, and whether price sits in lower third
  (accumulation bias) or upper third (distribution bias).

### 6.2 Volume behavior — the heart of Wyckoff
This carries the most weight (35). Effort (volume) vs. result (price movement):
- **Accumulation signs:** volume dry-up on declines into support; volume expansion on
  rallies off support; down-bars on low volume near the lows.
- **Distribution signs:** volume expansion on declines from resistance; volume dry-up on
  rallies into resistance; up-bars on low volume near the highs.
- Compare recent volume distribution at range extremes vs. the range average.
- Output: a directional volume score (positive = accumulation, negative = distribution)
  and a magnitude.

### 6.3 Spring / upthrust detection
- **Spring:** price briefly breaks below range support then closes back inside on a
  rejection wick (`spring_wick_pct`) — a bullish accumulation tell.
- **Upthrust:** mirror image above resistance — bearish distribution tell.
- Bonus weight if the false-break happened on a volume characteristic consistent with
  6.2 (e.g., spring on declining volume, or recovery on rising volume).

### 6.4 Phase bias & strategy score
- Combine sub-scores using `wyckoff.sub_weights` into a 0–100 score.
- Assign a **direction**: `accumulation` (long candidate) or `distribution` (short candidate),
  or `none`.
- Report the direction with the stronger evidence per ticker/timeframe. Both directions are
  actionable downstream and shown as tradeable setups.
- Emit a `StrategyResult` (score, direction, sub_scores, reason tags, metadata).

### 6.5 Liquidity gate (`universe.py`, inline at scan time)
- Compute 20-day average dollar volume; drop below `min_avg_dollar_volume` or
  `min_price`. Logged as skipped-with-reason.

## 7. Secondary confirmation — "higher conviction, kept simple"

These feed the Wyckoff strategy's own `confirmation` sub-score (weight 20 in
`wyckoff.sub_weights`). They are *within-strategy* corroboration, distinct from the
*cross-strategy* stacking that `combiner.py` will eventually do. All cheap, rules-based.
Kept short on purpose:

1. **Relative strength vs. SPY.** For accumulation, is the stock holding up better than the
   index on down/flat tape? Mirror for distribution. Strong "smart money positioned" tell.
2. **Volatility contraction.** Bollinger Band width or ATR contracting inside the range
   ("the coil") often precedes the markup/markdown. Cheap, high signal.
3. **Multi-timeframe agreement (cross-read, not recompute).** A ticker flagged the same
   direction on **both** Daily and Weekly gets a bonus — the single biggest free win. Since
   D and W run on different schedules, the running timeframe reads the **most recent stored
   result** for the other timeframe from `state.py` (e.g., a Daily run reads the last
   Weekly signal). It never recomputes the other timeframe inline.
4. **Trend context.** Accumulation is most meaningful after a prior downtrend; distribution
   after a prior uptrend. Penalize setups lacking the preceding move.

> Deliberately excluded for v1: order-flow/footprint, options flow, fundamentals. These add
> conviction but also cost/complexity — future phases (and several would arrive as their own
> *strategies* via §6, not as more confirmation factors here).

## 8. Output (`report.py`)

### 8.1 HTML dashboard (primary)
- Render `output/report_<YYYY-MM-DD>.html` from a Jinja2 template, plus copy/symlink to
  `output/latest.html`.
- **Ranked by composite score, descending.** Accumulation and distribution candidates in
  separate, clearly labeled sections.
- Each candidate card shows:
  - Ticker, exchange-prefixed symbol, direction, composite score (0–100).
  - **Score breakdown** (range / volume / spring / confirmation sub-scores) so you can see
    *why* it flagged at a glance.
  - Plain-English reason tags (e.g., "volume dry-up at support", "spring on Tue",
    "outperforming SPY", "flagged on both D + W").
  - An **embedded interactive TradingView Advanced Chart widget** for that symbol, so you
    inspect the chart in the same place — no app-switching.
- Self-contained: the page references TradingView's hosted embed script
  (`embed-widget-advanced-chart.js`) via `<script>` + symbol config. **No API key.**
- **Keep TradingView attribution visible** (license requirement for the free widget).
- Top-of-page run summary: timeframe(s), counts (scanned / flagged / skipped / errored),
  run timestamp.
- Lazy-load chart widgets (only instantiate when a card scrolls into view) so a report
  with many candidates stays responsive.

### 8.2 TradingView import file (secondary, near-free)
- Also write `output/watchlist_daily.txt` / `watchlist_weekly.txt`, one exchange-prefixed
  symbol per line (e.g., `NASDAQ:AAPL`), optional `###ACCUMULATION` / `###DISTRIBUTION`
  group markers. For the occasions you want the names inside your TV account.

### 8.3 Notification (Discord/Telegram) — newly-qualifying + invalidations only
Notification fires on **state transitions**, not the full list, to avoid noise (`state.py`
holds the prior run's qualifying set per timeframe):
- **NEW** — ticker qualifies (≥ threshold) that was not qualifying on the prior run of that
  timeframe. Every timeframe reports each newly-qualifying ticker at least once.
- **FAILED/INVALIDATED** — a previously-qualifying setup that has now dropped below
  threshold or whose structure broke (e.g., range violated, spring failed). This transition
  *is* notified, since a failing setup is actionable (exit/avoid).
- **Still-qualifying, unchanged** — not re-notified.
- Message contents: timeframe, counts of NEW and FAILED, the top NEW tickers with scores +
  direction, and a report link (`REPORT_BASE_URL` + filename if hosted; else summary + local
  path). Webhook URL and base URL from env/secrets, never committed.
- Suppress entirely if a run has no NEW and no FAILED transitions (configurable).

### 8.4 Signal log (`output/signals.csv`)
- Append every evaluated ticker (even sub-threshold) per run. Schema:
  `run_ts, ticker, timeframe, direction, composite_score, wyckoff_score, range_score,
  volume_score, spring_score, confirmation_score, rs_vs_spy, vol_contraction, mtf_agree,
  trend_context, data_quality_flag, made_watchlist, transition` where `transition` ∈
  {`new`, `continuing`, `failed`, none}.
- Per-strategy columns are namespaced so adding strategies extends (not breaks) the schema;
  `composite_score` is the combiner output, `wyckoff_score` the strategy's own.
- This is the audit trail, the dedup source, and the dataset for future backtesting AND
  cross-strategy correlation analysis (§7 / §6 combiner).

## 9. Scheduling (`.github/workflows/scan.yml`)

- **Daily scan:** cron after US market close (e.g., 22:00 UTC weekdays), accounting for
  the fact GitHub cron is UTC and not guaranteed to the minute.
- **Weekly scan:** cron Friday after close / Saturday.
- Steps: checkout → setup Python → install → run scanner → write report + import file +
  signals.csv → commit `output/` back to the repo (preserves history) → push
  Discord/Telegram notification.
- **Viewing the report:** simplest option is to open the committed `latest.html` from the
  repo. For a clickable mobile link in the notification, optionally publish `output/` via
  **GitHub Pages** (free) and set `REPORT_BASE_URL` to the Pages URL. Note: if published
  publicly, that satisfies the TV widget's "public, not paywalled" license condition; if
  you'd rather keep it private, skip Pages and open the file locally.
- Secrets (notify webhook URL, report base URL) via GitHub Secrets.

## 9A. State & dedup (`state.py`)

- Reads the prior run's results (from `signals.csv` and/or a small `state.json`) to support
  two things: **notification dedup** (§8.3) and the **multi-timeframe cross-read** (§7.3).
- Tracks, per timeframe, the set of currently-qualifying tickers and their last score so the
  next run can classify each as new / continuing / failed.
- Stores the latest result per timeframe so the *other* timeframe's run can read it without
  recomputation.
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
- Golden-file test for the import-file format and the report's data model (card dicts), so
  output-format changes are caught.
- A smoke test running the full scanner on ~10 tickers end to end.

## 12. Future phases (explicitly out of scope now, architected for)

- **Additional strategies** (momentum regime, relative strength, volatility) implementing
  the §6 `Strategy` interface — each a new file in `strategies/` + a config line.
- **Confirmation stacking**: combiner raises conviction when independent strategies agree.
- **Correlation-awareness in `combiner.py`**: measure pairwise correlation of strategy
  scores from `signals.csv`; down-weight clusters that are really one signal, so agreement
  means something. (This is *why* per-strategy scores are logged separately now.)
- **Backtesting harness**: replay `signals.csv` forward N bars to test whether high scores
  preceded markup/markdown. Must use a point-in-time universe to avoid survivorship bias
  (§4.1). The §8.4 logging is designed to enable this.
- Paid data source swap (Polygon) if intraday or higher data quality is later needed.
- Chart annotations on the embedded charts (range lines, spring marker).
- **Discord static chart preview (v1.1 nicety)**: render a PNG snapshot per top ticker
  (headless chart render) and attach it to the notification, so the channel shows a
  glanceable preview before clicking through to the interactive dashboard. Discord cannot
  host the interactive widget itself — it stays a notification surface, the HTML dashboard
  stays the inspection surface.
- IBKR integration as an *optional* extra output for users who keep TWS running.

## 13. Acceptance criteria (v1)

- [ ] Runs end-to-end on the sample universe on Daily and Weekly with no crashes.
- [ ] Produces a self-contained HTML dashboard, ranked by score, with a working embedded
      TradingView chart per flagged ticker and visible TV attribution.
- [ ] Both accumulation (long) and distribution (short) setups are shown, separated/labeled.
- [ ] Data-quality step excludes/flags bad bars and logs what it touched; never invents data.
- [ ] Writes the secondary TV import file that imports cleanly.
- [ ] Notification fires only on NEW and FAILED transitions; still-qualifying not re-sent.
- [ ] Every run appends a complete, schema-correct row per ticker to `signals.csv`, with
      per-strategy columns and a `transition` value.
- [ ] All thresholds, weights, and enabled strategies are driven by `config.yaml`.
- [ ] MTF agreement uses a cross-read of the other timeframe's stored result (no recompute)
      and is visible in the log and on the cards.
- [ ] Wyckoff implements the `Strategy` interface; `combiner.py` produces the composite
      (trivially, for one strategy) — adding a second strategy needs no engine change.
- [ ] No lookahead: a test confirms evaluation on bar N is unchanged by appending bar N+1.
- [ ] Scheduled GitHub Action runs unattended and commits output + sends notification.
