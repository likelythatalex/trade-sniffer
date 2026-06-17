# Wyckoff Accumulation/Distribution Scanner

A scheduled scanner that runs Wyckoff accumulation/distribution analysis across a universe of
liquid US equities on **Daily** and **Weekly** timeframes. High-conviction setups are ranked
into a single-file **HTML dashboard with embedded TradingView charts**, a TradingView-importable
watchlist is written, and a link is pushed to **Discord**. It **finds and flags candidates for
human review — it never places trades.**

Design details live in [SPEC.md](SPEC.md); working agreements in [CLAUDE.md](CLAUDE.md); domain
concepts + implementation status in [docs/appendix.md](docs/appendix.md).

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
| `report_<tf>_<date>.html` | Dated dashboard (ranked cards + embedded charts) |
| `latest_<tf>.html` | Newest dashboard for that timeframe — open this in a browser |
| `watchlist_<tf>.txt` | TradingView-importable symbol list |
| `signals.csv` | Append-only log of **every** evaluated ticker (the calibration/backtest dataset) |
| `state.json` | Prior-run qualifiers (drives dedup + the multi-timeframe cross-read) |

In the dashboard, each chart can be **drag-resized** (bottom edge) or **expanded fullscreen**
(⤢ button; exit with the ✕ button or `Esc`).

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

`.github/workflows/scan.yml` runs daily (after US close) and weekly (Saturday), commits the
results back to the repo, and notifies Discord. To enable notifications, add repository
**secrets** (Settings → Secrets and variables → Actions):

- `NOTIFY_WEBHOOK_URL` — your Discord webhook URL (where the message is POSTed).
- `REPORT_BASE_URL` *(optional)* — a public base URL where the report is hosted (e.g. GitHub
  Pages). Used only to build a clickable report link inside the Discord message; if unset, the
  message omits the link. **This is separate from the webhook.**

Test the workflow manually via the Actions tab → *scan* → *Run workflow*. Note: GitHub
disables scheduled workflows after ~60 days of repo inactivity (a commit re-arms them).

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
