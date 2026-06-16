# CLAUDE.md

Context and working agreements for any AI assistant (Claude Code, etc.) contributing to this repo.

## What this project is

A scheduled scanner that runs **Wyckoff accumulation/distribution analysis** across a
universe of liquid US equities on **Daily and Weekly** timeframes. When a chart shows
**high-conviction accumulation or distribution structure**, the ticker is added to a
**self-contained HTML dashboard with embedded interactive TradingView charts**, ranked by
conviction. A link to the new report is pushed to **Discord/Telegram**. A
TradingView-importable `.txt` watchlist is also written as a near-free secondary output.

The job re-runs on a schedule (per timeframe) via **GitHub Actions**.

**This tool finds and flags trades. It never places them.** No broker write access, ever.

## Core design decisions (do not silently change these)

| Area | Decision | Why |
|------|----------|-----|
| Data source | `yfinance` (v1) | Free, reliable for daily/weekly, deep history |
| Timeframes | Daily + Weekly only | Cleaner signals, lower noise, lower API load |
| Universe | Liquid large-caps + user custom list (`universe.txt`) | Liquidity makes volume analysis meaningful |
| Watchlist output | **HTML dashboard w/ embedded TradingView chart widgets** (primary); TV `.txt` import file (secondary) | Find + inspect in one place; no manual import; runs unattended |
| Notification | Discord/Telegram webhook with link to report | Lightweight, mobile-friendly, no email setup |
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
├── config.yaml              # thresholds, timeframes, enabled strategies + weights
├── universe.txt             # user-editable ticker list (one per line)
├── src/
│   ├── config.py            # load + validate config.yaml into typed objects
│   ├── universe.py          # build/filter the ticker universe (liquidity gate)
│   ├── data.py              # fetch + cache OHLCV
│   ├── data_quality.py      # detect/flag/repair-or-exclude bad bars (pure)
│   ├── strategies/
│   │   ├── base.py          # Strategy interface + StrategyResult dataclass
│   │   ├── registry.py      # name -> Strategy, discovered from config
│   │   └── wyckoff.py       # the ONLY strategy for v1 (range/volume/spring)
│   ├── combiner.py          # aggregate strategy sub-scores -> composite (weighted)
│   ├── scanner.py           # orchestrator: universe x timeframe x strategies
│   ├── state.py             # read prior run signals for dedup + MTF cross-read
│   ├── report.py            # build HTML dashboard + TV import file
│   └── notify.py            # Discord/Telegram push
├── templates/
│   └── report.html.j2       # Jinja2 template for the dashboard
├── output/
│   ├── report_YYYY-MM-DD.html   # dated dashboard w/ embedded charts
│   ├── latest.html              # symlink/copy of newest report
│   ├── watchlist_daily.txt      # secondary TV import file
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
  volume behavior, data cleaning) stays as pure functions returning typed dataclasses. No
  hidden state, no deep inheritance. Easier to test, easier to backtest.
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

- **Pure functions for analysis.** Strategy internals, `data_quality.py`, and `combiner.py`
  take data, return scores/dataclasses. No I/O or network calls inside them — testable and
  backtestable.
- **Everything tunable lives in `config.yaml`.** No magic numbers buried in code. Enabled
  strategies and their weights live there too.
- **Every signal is logged to `signals.csv`** with its full per-strategy score breakdown,
  even sub-threshold ones. This is the dataset for validating the scanner AND for the
  future correlation analysis.
- **Fail soft per-ticker.** One bad ticker (delisted, no data, failed quality check) must
  never kill the run. Catch, log, continue.
- **Data quality is conservative.** `data_quality.py` detects bad bars (zero/null volume,
  absurd spikes, dup timestamps, split-adjustment mismatches), logs what it touched, and
  **repairs only mechanically-obvious issues or excludes the ticker** — it never invents
  data to fill gaps. A faked volume climax is worse than a skipped ticker.
- **Idempotent runs.** Re-running the same scan on the same day overwrites cleanly; no
  duplicate entries.
- **Dedup / notification state (`state.py`).** Notify only on *newly* qualifying tickers,
  with two exceptions: (a) a ticker not yet seen on a given timeframe is new; (b) if a
  previously-flagged setup **fails/invalidates**, that transition is itself notified. A
  still-qualifying ticker is not re-notified.
- **Multi-timeframe cross-read.** Daily and Weekly run on different schedules. A Daily run
  reads the **most recent Weekly** result from prior state (does not recompute it) to apply
  the agreement bonus, and vice versa.
- **No lookahead.** Only use data available at the close of the bar being evaluated.
  Non-negotiable, and pre-validates the future backtester.

## Wyckoff scope (keep it honest)

Wyckoff is partly discretionary; full automated "phase labeling" is hard and noisy.
We deliberately scope to **detecting the structural fingerprints** of accumulation and
distribution rather than claiming to label every Wyckoff event perfectly:

- Trading range detection (price consolidating after a trend)
- Volume behavior within the range (the heart of Wyckoff: effort vs. result)
- Spring / upthrust style false-breakouts at range extremes
- Volume-dry-up vs. volume-climax characterization
- A composite **conviction score (0–100)**, not a binary call

We are flagging *candidates for human review*, not issuing verdicts. The README and any
user-facing text must reflect that framing.

## Things to confirm before adding

- Do NOT add intraday timeframes without discussing — yfinance intraday is unreliable and
  changes the whole data-quality story.
- Do NOT add automated order placement.
- Do NOT scrape TradingView or use unofficial private endpoints. The HTML dashboard uses
  TradingView's **free public embed widgets** only (script tag + symbol, no API key).
  Keep the required TradingView attribution visible in the report.

## Definition of done for any change

1. New analysis logic has a unit test with a hand-checked fixture.
2. `signals.csv` schema is preserved or migrated (don't break the log).
3. Runs clean end-to-end on the sample universe locally.
4. No secrets committed; webhook URLs come from GitHub Secrets / env vars.
