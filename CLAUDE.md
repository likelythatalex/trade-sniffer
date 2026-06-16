# CLAUDE.md

Context and working agreements for any AI assistant (Claude Code, etc.) contributing to this repo.

## What this project is

A scheduled scanner that runs **Wyckoff accumulation/distribution analysis** across a
universe of liquid US equities on **Daily and Weekly** timeframes. When a chart shows
**high-conviction accumulation or distribution structure**, the ticker is added to a
**single-file HTML dashboard (no build step) with embedded interactive TradingView charts**,
ranked by conviction. A link to the new report is pushed to **Discord** (Telegram deferred
to a future phase). A TradingView-importable `.txt` watchlist is also written as a near-free
secondary output.

The job re-runs on a schedule (per timeframe) via **GitHub Actions**.

**This tool finds and flags trades. It never places them.** No broker write access, ever.

## Core design decisions (do not silently change these)

| Area | Decision | Why |
|------|----------|-----|
| Data source | `yfinance` (v1) | Free, reliable for daily/weekly, deep history |
| Timeframes | Daily + Weekly only | Cleaner signals, lower noise, lower API load |
| Weekly data | **Fetched natively** (`interval="1wk"`), not resampled from daily | The daily lookback window is too short to reconstruct weekly history |
| Per-timeframe params | **Wyckoff params resolved per timeframe** (`defaults` + required `per_timeframe.weekly` overrides) | A 60-*day* range ≠ a 60-*week* range; scalar params would produce bad weekly signals |
| Thresholds | **Relative, per-stock** — Wyckoff high/low/narrow/wide defined vs. each stock's own rolling distribution; config holds ratios/percentiles, not absolutes | Absolute volume/spread thresholds are meaningless across a universe (does NOT apply to the liquidity gate, which is an intentional absolute floor) |
| Normalization | **Standalone `features.py`** between data and strategies; pipeline `data → data_quality → features → strategy` | Strategy-agnostic + reusable; quality before normalization so bad bars don't poison rolling baselines |
| Universe | Liquid large-caps + user custom list (`universe.txt`, bare tickers) | Liquidity makes volume analysis meaningful; exchange prefix is resolved at fetch time |
| Watchlist output | **HTML dashboard w/ embedded TradingView chart widgets** (primary, per-timeframe file); TV `.txt` import file (secondary) | Find + inspect in one place; no manual import; runs unattended |
| Notification | **Discord webhook** with link to report (v1) | Lightweight, mobile-friendly, no email setup; Telegram deferred but `notify.py` is channel-pluggable |
| Scheduling | GitHub Actions (cron) | Free, runs when laptop is off, version-controlled |
| Language | Python 3.11+ | Ecosystem for data + TA |
| Trade execution | **None** | Out of scope by design |

## Repo layout

```
wyckoff-scanner/
├── CLAUDE.md
├── SPEC.md
├── README.md
├── requirements.txt
├── config.yaml              # thresholds, timeframes, per-timeframe params, enabled strategies + weights
├── universe.txt             # user-editable ticker list (one bare ticker per line)
├── symbol_overrides.csv     # optional ticker,exchange corrections for TV symbol prefix
├── docs/
│   ├── wyckoff_methodology.md  # VSA→relative-feature rules; signal defs as [VERIFY] stubs
│   └── appendix.md             # domain concepts + implementation-status reference
├── src/
│   ├── config.py            # load + validate config.yaml into typed objects; resolve per-timeframe params
│   ├── universe.py          # build/filter the ticker universe (liquidity gate)
│   ├── data.py              # fetch + cache OHLCV (weekly native); resolve exchange prefix; fetch corporate actions; fetch SPY
│   ├── data_quality.py      # detect/flag/repair-or-exclude bad bars (pure; corporate actions passed in)
│   ├── features.py          # per-stock normalization -> relative features (pure; strategy-agnostic)
│   ├── strategies/
│   │   ├── base.py          # Strategy interface + StrategyResult dataclass
│   │   ├── registry.py      # name -> Strategy, discovered from config
│   │   └── wyckoff.py       # the ONLY strategy for v1 (range/volume/spring)
│   ├── combiner.py          # aggregate strategy sub-scores -> composite (weighted)
│   ├── scanner.py           # orchestrator: universe x timeframe x strategies
│   ├── state.py             # read prior run signals for dedup + MTF cross-read
│   ├── report.py            # build HTML dashboard + TV import file
│   └── notify.py            # Discord push (channel-pluggable for future Telegram)
├── templates/
│   └── report.html.j2       # Jinja2 template for the dashboard
├── output/
│   ├── report_<tf>_YYYY-MM-DD.html # dated dashboard per timeframe, w/ embedded charts
│   ├── latest_daily.html           # copy/symlink of newest daily report
│   ├── latest_weekly.html          # copy/symlink of newest weekly report
│   ├── watchlist_daily.txt         # secondary TV import file
│   ├── watchlist_weekly.txt
│   └── signals.csv          # full log w/ scores for review + later backtesting
├── tests/
└── .github/workflows/scan.yml
```

## Architecture philosophy (how much OOP)

**Object-oriented at one seam, functional everywhere else.** This is a solo-maintained
tool — the goal is a future-proof foundation that stays simple to manage, not an
enterprise framework.

- **The one abstraction that earns its keep: `Strategy`.** `strategies/base.py` defines a
  small interface — each strategy implements `evaluate(df, context) -> StrategyResult`.
  Wyckoff is the only strategy in v1, but the interface means future strategies (momentum
  regime, relative strength, volatility) slot in without touching the engine. A new
  strategy = one new file in `strategies/` + a line in `config.yaml`.
- **Everything else is plain functions + `@dataclass`.** Analysis logic (range detection,
  volume behavior, data cleaning, **per-stock normalization in `features.py`**) stays as pure
  functions returning typed dataclasses/frames. No hidden state, no deep inheritance. Easier
  to test, easier to backtest. `features.py` is strategy-agnostic and reusable by any future
  strategy; the strategy receives its output as the **feature frame via `context`** (raw
  OHLCV `df` + precomputed features), and applies no absolute volume/spread thresholds.
- **`registry.py` maps strategy name → class**, discovered from config, so `scanner.py`
  never hardcodes which strategies exist.
- **`combiner.py` aggregates** the per-strategy sub-scores into the composite. v1 is a
  simple weighted sum (weights from config). It is the designated home for future
  correlation-awareness (see below) — keep that logic here, not scattered.

## Multi-strategy & correlation (designed-for, not built yet)

v1 scope is **Wyckoff only.** But the architecture anticipates stacking independent
strategies to raise/lower conviction. Two distinct ideas, kept separate on purpose:

1. **Confirmation stacking** — multiple strategies agreeing boosts the composite. This is
   the valuable, intended direction. The `Strategy` interface + `combiner.py` exist for it.
2. **Correlation awareness** — stacking only means something if the strategies are
   *independent*. Three trend-flavored signals agreeing is one signal counted thrice, not
   confirmation. FUTURE work in `combiner.py`: measure pairwise correlation of strategy
   scores over history (`signals.csv` is the dataset) and down-weight correlated clusters.

Do not build #2 yet. Do keep `combiner.py` and `StrategyResult` shaped so it can be added
without a refactor (i.e., every strategy emits a normalized 0–100 directional score +
metadata, and the composite is computed in one place).

## Position direction

Both **long (accumulation)** and **short (distribution)** are actionable. The dashboard
presents both directions as tradeable setups, clearly separated and labeled. Distribution
is not merely an "avoid" flag — it is a short/put candidate.

## Conventions

- **Pure functions for analysis.** Strategy internals, `data_quality.py`, `features.py`, and
  `combiner.py` take data, return scores/dataclasses/frames. No I/O or network calls inside
  them — testable and backtestable. External inputs (e.g., corporate-action data for split
  checks) are **passed in by `data.py`**, never fetched inside the pure modules.
- **Fixed pipeline order: `data → data_quality → features → strategy`.** Quality runs
  *before* normalization so a bad bar can't poison the rolling volume/spread baseline.
  Strategies never see un-normalized data as a threshold input.
- **Relative thresholds, never absolute.** Every Wyckoff "high/low volume" or "narrow/wide
  spread" call is defined against the stock's own rolling distribution (`volume_ratio`,
  `volume_pctile`, `spread_atr`, `spread_pctile`, `close_position` from `features.py`).
  Config holds ratios/percentiles. These numbers are calibration candidates ([TUNABLE]/
  [VERIFY] in `docs/wyckoff_methodology.md`), not final truths. **Exception:** the liquidity
  gate (`universe.py`) stays an absolute dollar-volume/price floor by design — it is a
  universe filter, not a signal threshold.
- **Everything tunable lives in `config.yaml`.** No magic numbers buried in code. Enabled
  strategies and their weights live there too. **Wyckoff params are resolved per timeframe**
  (`defaults` merged with `per_timeframe[tf]`); `config.py` does the merge and strategies
  read the resolved values from `context`. The relative VSA thresholds (`high_volume_ratio`,
  `narrow_spread_atr`, `volume_pctile_high`, `no_demand_supply_median_window`,
  `range_extreme_fraction`, `spring_lookback`, `spring_snapback_bars`, `trend_lookback`, …)
  and `features.baseline_window` live in this same per-timeframe structure — all calibration
  seeds, `[TUNABLE]` against `signals.csv`.
- **One definition of "near support/resistance."** It is the §6.1 range-position output
  (lower/upper `range_extreme_fraction` of the range). Don't introduce a second "near"
  threshold anywhere. No Demand/No Supply uses the **rolling-median** volume form (not
  "previous N bars"). The full harmonic volume rule (impulse/correction legs) is FUTURE; v1
  trend-context is the simple `trend_lookback` price-vs-MA measure.
- **Every signal is logged to `signals.csv`** with its full per-strategy score breakdown,
  even sub-threshold ones, **plus the normalized `feat_*` features at the evaluated bar** so
  thresholds can be calibrated later. This is the dataset for validating the scanner AND for
  the future correlation analysis. `transition` ∈ {new, continuing, failed, none}. Extending
  the schema = version bump + migrate (per definition-of-done).
- **Fail soft per-ticker.** One bad ticker (delisted, no data, failed quality check) must
  never kill the run. Catch, log, continue.
- **NaN degrades gracefully, never propagates.** `features.py` emits NaN on degenerate bars
  (zero range, zero ATR/median); the dependent signal then *abstains* (zero contribution, no
  flag). Sub-score and composite math must never propagate NaN — every evaluated ticker gets
  a finite score, never an "unscorable" result. (SPEC §6.4.)
- **Sub-score mapping is a calibration seed.** Which signal feeds which sub-score is pinned
  first-pass in SPEC §6.4 / methodology §5; intra-sub-score weights start equal and are
  `[TUNABLE]` against `signals.csv`. Don't treat the seed weights as final.
- **Data quality is conservative.** `data_quality.py` detects bad bars (zero/null volume,
  absurd spikes, dup timestamps, split-adjustment mismatches), logs what it touched, and
  **repairs only mechanically-obvious issues or excludes the ticker** — it never invents
  data to fill gaps. A faked volume climax is worse than a skipped ticker.
- **Idempotent runs.** Re-running the same scan on the same day overwrites cleanly; no
  duplicate entries.
- **Dedup / notification state (`state.py`).** Discord notify only on *newly* qualifying
  tickers, with two exceptions: (a) a ticker not yet seen on a given timeframe is new; (b)
  if a previously-flagged setup **fails/invalidates**, that transition is itself notified. A
  still-qualifying ticker is not re-notified. On cold start (no prior state for a timeframe),
  seed state and send a condensed summary rather than a full NEW flood; suppress entirely
  when there are no NEW/FAILED transitions (`notify.suppress_empty`).
- **Multi-timeframe cross-read.** Daily and Weekly run on different schedules. A Daily run
  reads the **most recent Weekly** result from prior state (does not recompute it) to apply
  the agreement bonus, and vice versa. If the other timeframe has no stored result yet, MTF
  agreement is **neutral** (no bonus/penalty) and shows "n/a".
- **No lookahead.** Only use data available at the close of the bar being evaluated.
  Non-negotiable, and pre-validates the future backtester.

## Wyckoff scope (keep it honest)

Wyckoff is partly discretionary; full automated "phase labeling" is hard and noisy.
We deliberately scope to **detecting the structural fingerprints** of accumulation and
distribution rather than claiming to label every Wyckoff event perfectly:

- Trading range detection (price consolidating after a trend) — v1 boundary = support/
  resistance band (SPEC §6.1); climax-anchored boundaries are a FUTURE refinement, with
  climax informing scoring *within* the range, not the boundary
- Volume behavior within the range (the heart of Wyckoff: effort vs. result)
- Spring / upthrust style false-breakouts at range extremes
- Volume-dry-up vs. volume-climax characterization
- A composite **conviction score (0–100)**, not a binary call

All of the above are computed over the **relative features** from `features.py` (never
absolute volume/spread levels); the specific VSA signal definitions and their numeric
thresholds live in `docs/wyckoff_methodology.md` as `[VERIFY]`/`[TUNABLE]` stubs to confirm
and calibrate — do not treat them as final.

We are flagging *candidates for human review*, not issuing verdicts. The README and any
user-facing text must reflect that framing.

## Things to confirm before adding

- Do NOT add intraday timeframes without discussing — yfinance intraday is unreliable and
  changes the whole data-quality story.
- Do NOT add automated order placement.
- Do NOT add regime-aware feature baselines in v1 — `features.py` uses a plain rolling
  window; the post-earnings ATR-inflation caveat is acknowledged and deferred.
- Do NOT reintroduce absolute volume/spread thresholds anywhere in the strategy — they must
  be relative features from `features.py`. (The liquidity gate's absolute floor is the one
  intentional exception.)
- Do NOT add a second notification channel (Telegram) in v1 — keep `notify.py`
  channel-pluggable but ship Discord-only.
- Do NOT scrape TradingView or use unofficial private endpoints. The HTML dashboard uses
  TradingView's **free public embed widgets** only (script tag + symbol, no API key).
  Keep the required TradingView attribution visible in the report.

## Definition of done for any change

1. New analysis logic has a unit test with a hand-checked fixture.
2. `signals.csv` schema is preserved or migrated (don't break the log).
3. Runs clean end-to-end on the sample universe locally.
4. No secrets committed; webhook URLs come from GitHub Secrets / env vars.
5. If the change alters how a concept is implemented (or flips its status), update the
   matching `docs/appendix.md` entry — status tag + "How it's implemented here" line +
   module/config reference — in the same change. Methodology stubs in
   `docs/wyckoff_methodology.md` stay as `[VERIFY]`/`[TUNABLE]` until confirmed; don't
   harden them into asserted truths.
