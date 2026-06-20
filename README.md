# Wyckoff Accumulation/Distribution Scanner

A scheduled scanner that runs Wyckoff accumulation/distribution analysis across a universe of
liquid US equities on **Daily** and **Weekly** timeframes. High-conviction setups are ranked
into a single-file **HTML dashboard with embedded TradingView charts**, a TradingView-importable
watchlist is written, and a link is pushed to **Discord**. It **finds and flags candidates for
human review — it never places trades.**

Design details live in [SPEC.md](SPEC.md); working agreements in [CLAUDE.md](CLAUDE.md); domain
concepts + implementation status in [docs/appendix.md](docs/appendix.md); what's next + the
prioritized backlog in [ROADMAP.md](ROADMAP.md).

## ⚠️ Disclaimer

A personal, educational project, provided **as-is with no warranty**. It is **not financial
advice**, and nothing it outputs is a recommendation to buy or sell anything. It **finds and
flags candidates for human review — it never places trades.** Signals can be wrong, backtests
carry their own biases, and markets do what they want. Do your own research; trade your own
money at your own risk.

## Setup

Python 3.13 (3.11+ works). From the repo root:

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate        Linux/macOS:  source .venv/bin/activate
pip install -r requirements.txt
pytest -q                                  # 100 tests
```

## Running a scan

```bash
# All configured timeframes (daily + weekly):
python -m src.scanner

# A single timeframe:
python -m src.scanner --timeframe daily
```

### On-demand scan (ad-hoc exploration)

```bash
# Scan a specific list instead of universe.txt:
python -m src.scanner --timeframe daily --tickers AAPL,MSFT,COIN

# Bypass the liquidity filter (for names you already trust):
python -m src.scanner --tickers BTC-USD --no-liquidity-gate

# Lower the watchlist cutoff so more candidates show (default is 70):
python -m src.scanner --timeframe daily --threshold 30
```

`python -m src.scanner --help` lists all flags. Scheduled runs ignore these flags (defaults).

### What a run produces (in `output/`)

| File | What it is |
|------|-----------|
| `report_<tf>_<date>.html` | Dated dashboard (ranked list + one annotated chart) |
| `latest_<tf>.html` | Newest dashboard for that timeframe — open this in a browser |
| `index.html` | Landing page linking the latest daily/weekly dashboards |
| `watchlist_<tf>.txt` | TradingView-importable symbol list |
| `signals.csv` | Append-only log of **every** evaluated ticker (the calibration/backtest dataset) |
| `state.json` | Prior-run qualifiers (drives dedup + the multi-timeframe cross-read) |

The dashboard shows a ranked candidate list beside **one shared annotated chart** (TradingView
Lightweight Charts™, fed by data embedded in the page): click a candidate to load it, with the
range band and spring/upthrust marker drawn on. Each candidate also has an "open in
TradingView ↗" link for deep manual analysis.

## Backtesting (offline)

An on-demand tool that asks: **does a higher conviction score predict a better forward
outcome?** It re-scores history with the *same* production pipeline and reports the
Information Coefficient (does the score rank outcomes?), forward returns by score bucket,
hit-rate lift, and per-sub-score IC. It never runs on the schedule and never trades.

```bash
python -m src.backtest --timeframe daily --tickers AAPL,XOM,KO --horizons 5,10,20
python -m src.backtest --timeframe daily --limit 50      # first 50 universe names
```

Results (markdown + raw CSV) are written to `backtest_results/` (gitignored). **Caveat:**
the replay mode scores today's universe, so it carries *survivorship bias* — use it for
calibration/iteration, not as an unbiased verdict (the report says so too). See
[ROADMAP.md](ROADMAP.md) for the unbiased (live-`signals.csv`) Phase 2.

## Agent reviewer (optional, off by default)

A proactive, objective due-diligence pass on **newly-flagged** setups: at scan time it asks an
LLM for a skeptical second opinion (verdict + concerns) and bakes the notes into the dashboard.
It reviews the signal, **never gives trading advice**, and is strategy-agnostic.

Enable it in [config.yaml](config.yaml) (`review.enabled: true`) and set the `ANTHROPIC_API_KEY`
secret. It's **cost-bounded** for the public repo: NEW transitions only, a hard
`max_reviews_per_run` cap, a cheap model by default (Haiku), bounded output, and a
`reviews.json` cache (keyed `timeframe:ticker`) so continuing setups and same-day re-runs never
re-spend. No key or a failed call simply omits the review.

### Local LLM via Ollama (optional, free + private)

The reviewer provider is pluggable: `anthropic` (cloud) or `ollama` (a local GPU). The committed
config stays `anthropic` so the **scheduled CI review** is automated and reachable (a cloud runner
can't reach a home GPU). For **local** runs you flip to Ollama with env vars — no file edits, no
API key, and **trade data never leaves your machine** (the big win for the private `journal review`):

```bash
# one-time: pull a small instruct model (7-8B fits a 10-12GB card comfortably)
ollama pull qwen2.5:7b
# then point local runs at it:
export REVIEW_PROVIDER=ollama REVIEW_MODEL=qwen2.5:7b   # OLLAMA_BASE_URL defaults to localhost:11434
python -m src.journal review            # now runs on your GPU, fully private
```

A down/unreachable server just fail-softs (the review is omitted). Maintaining Ollama (install,
`ollama pull`, drivers, keeping it up) is **local infrastructure — out of scope for this repo**;
the repo only speaks HTTP to it.

## Private trade journal (local-only)

The dashboard suggests a plan (entry/stop/target/size + a management playbook) for each
flagged setup — **suggestions for human review, never executed.** To track the trades you'd
actually take, there's a **local, private** journal CLI. It writes to a **gitignored**
`journal.csv` and runs only on your machine — never committed, never published, never in CI
(the repo is public, so trades stay off it by design).

```bash
python -m src.journal add KO short 73.59 79.48 63.69 170 --source "wyckoff daily"  # ticker dir entry stop target size
python -m src.journal list --status open
python -m src.journal close 1 64.10 --notes "hit target"
python -m src.journal report     # outcome of each trade vs price history (stop/target-first, realized R, MFE/MAE)
python -m src.journal review     # private post-trade reflection on closed trades (needs ANTHROPIC_API_KEY)
python -m src.journal html       # render a private journal page (gitignored journal_report.html)
```

`html` writes a local, **gitignored** `journal_report.html` (trades + outcomes + reflections +
win-rate/expectancy summary). To view it away from your desk *without* exposing trades, run it
locally and reach the file over [Tailscale](https://tailscale.com) — no public hosting, no
backend. The fuller hosting path (a small web app, only when you need view-time interaction)
is the phased plan in [ROADMAP.md](ROADMAP.md) → "Hosting & UI evolution".

`review` reuses the agent reviewer with a reflection rubric that judges **process vs outcome**
(a good process can lose, a bad one can win); it's capped, cached (gitignored
`trade_reviews.json`), and **never gives trading advice** — reflective notes on past trades only.

## Editing the universe

[universe.txt](universe.txt) — one bare ticker per line (no exchange prefix), `#` for comments.
Add/remove lines freely; the liquidity gate skips illiquid names with a logged reason.
SPY is always fetched as the relative-strength benchmark, so it doesn't need listing.

## Configuration

All thresholds, weights, lookbacks, and enabled strategies live in [config.yaml](config.yaml)
(no magic numbers in code). Wyckoff params are resolved per timeframe (`defaults` merged with
`per_timeframe`). The numeric thresholds are **calibration seeds**, tuned later against
`signals.csv` — not final truths.

## Scheduling (GitHub Actions)

`.github/workflows/scan.yml` runs daily (after US close) and weekly (Saturday). It restores
the persistent state from `gh-pages`, runs the scan, **publishes all output to the `gh-pages`
branch** (served by GitHub Pages — so `main` stays code-only), and notifies Discord.

- The dashboard is published at **`https://<user>.github.io/trade-sniffer/latest_daily.html`**
  (and `latest_weekly.html`). `signals.csv` and `state.json` accumulate on `gh-pages`.
- Add repository **secrets** (Settings → Secrets and variables → Actions):
  `NOTIFY_WEBHOOK_URL` — your Discord webhook; `ANTHROPIC_API_KEY` — **optional**, only needed
  if you enable the agent reviewer (see below). `REPORT_BASE_URL` is set in the workflow to the
  Pages URL (so the Discord message links to the live dashboard); no secret needed for it.
- GitHub Pages requires the repo to be **public** on a free plan (private Pages needs a paid
  plan). `tests.yml` runs the suite on every push/PR.

Test manually via the Actions tab → *scan* → *Run workflow*. Note: GitHub disables scheduled
workflows after ~60 days of repo inactivity (a commit re-arms them).

### Off-schedule / non-trading-day runs

A scan always evaluates the **last available closed bar** for the timeframe — so running on a
weekend/holiday uses the most recent trading day (daily) or completed week (weekly). Running
*during* an open session may include a partial in-progress bar; prefer running after the close.

## Local testing of notifications

```bash
# Linux/macOS:
NOTIFY_WEBHOOK_URL="https://discord.com/api/webhooks/..." python -m src.scanner --timeframe daily --threshold 30
# Windows PowerShell:
$env:NOTIFY_WEBHOOK_URL="https://discord.com/api/webhooks/..."; python -m src.scanner --timeframe daily --threshold 30
```
