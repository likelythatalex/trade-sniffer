# v2 Rewrite Notes (seed)

A carry-over for the dedicated **v2 session**. v1 (this repo) is *frozen as the live
data-accruer*; v2 is a **ground-up, unified, local + hostable web app that runs on PC + mobile**,
built deliberately in its own session. This file is a springboard + the open decisions — expand
it there. (See the `rewrite-v2-direction` project memory for the short version.)

## Guiding principles

- **Rewrite the shell, keep the brain.** The pure, tested, I/O-free domain cores are reused as a
  library — not re-derived: `strategies/`, `features`, `data_quality`, `combiner`, `episodes`,
  `backtest/`, `trade_plan` / `trade_outcome`, `market_context`, `sentiment*`, `insider*`. v2 is
  new **web + storage + UI** around them. This is what de-risks the rewrite (most logic isn't
  rewritten) and keeps the rewrite-trap at bay.
- **Schema continuity.** Keep `signals.csv` / `market.csv` semantics continuous (or a clean
  versioned migration) so the accrued calibration dataset is one unbroken series across v1→v2.
- **Public vs private, static vs dynamic.** A backend is only needed for view-time
  compute/mutation. The journal is **private trades — never public**. (Carried from the ROADMAP
  "Hosting & UI evolution" reasoning.)
- **YAGNI / smallest-first.** Build in thin vertical slices; name the trigger before each.

## Repo & privacy

- v2 = a **separate private repo** (a *branch* of this public repo is **not** private).
- v1 stays in **this public repo**, scheduled scan running, publishing to gh-pages — so data
  keeps accruing while v2 is built. Retire v1 only once v2 reaches parity.
- **Cron caveat:** GitHub disables the scheduled workflow after ~60 days of repo inactivity. Keep
  this repo nudged (any commit re-arms it) or accrual silently stops.
- Reusing the brain across two repos: simplest early option is to **copy the pure core modules**
  into v2; promote to a shared installable package only if it earns it.

## Data layer & migration (do this early — it's cheap)

The accrued data is just CSVs with **stable, versioned, self-migrating** schemas:
`output/signals.csv` (schema v5, columns in `report.SIGNALS_COLUMNS`), `output/market.csv`
(`report.MARKET_COLUMNS`), and the private repo-root `journal.csv`. Treat them as the
**interchange format**.

- **v2 store: SQLite** (queryable; one file; fits a local + hostable app; Postgres is only Phase C
  "if it strains"). Tables mirror the CSV columns: `signals`, `market`, `journal` (+ derived views).
- **Importer: a small, idempotent CSV→SQLite loader**, keyed on `(run_ts, ticker)` for signals /
  `(run_ts, timeframe)` for market, so it can be **re-run** as v1 keeps appending. ~50 lines/file
  since the columns are already defined in code.
- **During the build:** v1 keeps writing the CSVs to gh-pages (public URLs, e.g.
  `https://<user>.github.io/trade-sniffer/signals.csv`, or via the gh-pages branch). v2 pulls a
  snapshot and imports; **final re-import at cutover**.
- **Writing new data in v2:** v2 owns its SQLite and appends new scans / journal mutations there.
  Optionally also export to the v1 CSV columns for round-trip / continuity of the calibration set.
- Net: migration is a script, not a project — and it's the natural moment to move off flat files.

## Candidate "in the guts" review areas (springboard — not decisions)

- Storage → SQLite (above) + a thin repository/DAO layer so the cores stay I/O-free.
- A **data-source abstraction** beyond yfinance (the swap has been looming) + caching strategy.
- `config.yaml` structure — it's grown large (per-strategy, reviewers, market, trade_plan…);
  revisit grouping / env-overridability.
- A **first-class calibration pipeline** for the weight-0 strategies (momentum / news_sentiment /
  insider) — the actual payoff of all this accrual; turn the backtest/event-study/sweep tools into
  a repeatable "recalibrate" flow.
- **Mobile-first / installable PWA** UI from the start; responsive; offline-tolerant reads.
- **Auth + multi-device** (single-user assumption?); how the private app is reached (Tailscale /
  Cloudflare Tunnel / VPS).
- Notifications: Discord today; v2's own scheduler can push (PWA push / Discord / Telegram).
- Reuse the existing **test suite** philosophy (pure fixtures, hermetic, network-mocked).

## Industry feature survey (v2 MUST actively propose — user priority)

Explicit v2 task: do a feature-discovery pass on what's **popular/beloved in the industry** —
from BOTH a **quant/technical** lens and a **professional discretionary trader** lens — and
propose what to adopt. The list below is a *seed to prioritize*, not a commit-all. Filter every
candidate against the project's invariants: **flag candidates for human review, NEVER execute
trades** (order routing / broker write is permanently out of scope), point-in-time / no-lookahead,
and YAGNI (each feature must earn its place). "(have)" = a v1 building block to extend.

### Quant / technical lens
- **Validation rigor:** walk-forward + out-of-sample (have `--oos-frac`), purged / combinatorial
  k-fold CV (López de Prado), Monte-Carlo / bootstrap of the equity curve & drawdowns,
  multiple-testing correction / **deflated Sharpe** (many `[TUNABLE]` knobs → real overfit risk),
  IC decay over horizon (have IC).
- **Cost realism:** transaction-cost model — commission + **slippage** — in the plan-sim (currently
  ignored), turnover + **capacity** analysis.
- **Performance/risk suite:** Sharpe / Sortino / Calmar, CAGR, max drawdown + duration, profit
  factor, expectancy, exposure, **R-multiple distribution** (have MFE/MAE + realized R).
- **Position sizing:** volatility targeting, fractional **Kelly**, ATR-based (have ATR),
  correlation-aware / portfolio **"heat"** caps.
- **Portfolio view:** correlation matrix, sector / factor exposure, concentration limits, beta &
  excess-vs-SPY (have excess returns).
- **Regime / factor:** volatility regime (VIX), factor tilts (momentum / value / quality / size),
  market regime + breadth (have Phase 1).
- **Signal hygiene:** correlation-aware strategy stacking (on roadmap), point-in-time data
  versioning, a reproducible feature store.

### Professional discretionary trader lens
- **Watchlists** (multiple, custom) + **alerts** (level cross / signal fires / transition) via
  push / PWA / Discord / Telegram.
- **Charting pros expect:** VWAP + **anchored VWAP**, volume profile / market-profile (TPO),
  multi-pane **multi-timeframe confluence**, relative-strength line, **replay mode**, drawing
  tools, saved layouts, hotkeys.
- **Screeners:** saveable custom filters (extend the new search/filter), sector / industry
  **heatmaps**, a breadth dashboard.
- **Calendars:** **earnings** + economic-event proximity (flag / avoid around earnings),
  seasonality.
- **Journal pro features** (build on the journal): setup **tags / taxonomy**, screenshot attach,
  R-multiple + **equity curve**, **calendar P&L heatmap**, win/loss streaks, time-of-day & by-setup
  "**edge**" analytics, pre/post-trade **checklists**, notes / psychology.
- **Explainability:** per-flag "why" (have reasons + agent reviewer), suggested plan + management
  (have).
- **Risk dashboard:** open risk / daily loss limit / max positions / "don't double up"
  correlation warnings.
- **UX:** mobile parity (PWA), dark mode (have), fast keyboard nav, customizable layout.

### How to use this in v2
Shortlist by **value × fit × effort**; sequence as thin vertical slices; keep the "never trades" +
no-lookahead invariants; prefer features that **compound existing assets** (the accrued
`signals.csv`, the journal, the backtest tooling) over net-new subsystems; and **re-survey
periodically** — the industry moves. The v2 session should produce a ranked proposal from this,
not silently inherit it.

## Decisions (resolved 2026-06-22)

1. **Client = responsive web app / PWA**, served by the Python backend. One codebase, installable
   on phone, brain stays server-side. No native mobile.
2. **v2 owns scheduling** (it runs the unattended scans itself — replacing v1's GitHub Actions
   cron). Implications:
   - Needs an **always-on host** — a local-only-when-PC-on install is NOT enough for unattended
     scans. So: a small VPS / always-on box / managed host, **privately gated** (Tailscale or
     Cloudflare Tunnel, or VPS + auth) since v2 is fully private.
   - Backend scheduler (APScheduler / host cron / a worker) with per-timeframe schedules (daily
     after US close, weekly Sat), mirroring v1.
   - **Ops shift:** you now own uptime, cost, and **backups of the SQLite store** (it becomes the
     single source of truth + holds the accrued dataset — gh-pages no longer does).
3. **Fully private** (only your devices, e.g. behind Tailscale). No public surface in v2.
   - v1's **public** gh-pages scanner keeps running **until v2's scheduler is live + validated**,
     then: final data import → **retire v1** (archive the public repo / leave it static).

### Still to decide at the v2 session
- **Hosting target + private access:** home always-on box vs $5 VPS vs managed (Fly.io/Render),
  and Tailscale vs Cloudflare Tunnel vs VPS+auth. (Budget/uptime/comfort call.)
- **SQLite confirmed** as the store; **single-user** assumed — confirm.
- Cutover plan: run v1 (accruing) and v2 in parallel; avoid double-writing one store (v1 writes
  CSVs, v2 imports); flip the schedule to v2 once validated.
