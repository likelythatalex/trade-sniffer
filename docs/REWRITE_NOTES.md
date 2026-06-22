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
- Scheduling: keep the scan in CI vs v2 owning it (needs always-on host) — see open decisions.
- Notifications: Discord today; revisit (push / PWA notifications / Telegram).
- Reuse the existing **test suite** philosophy (pure fixtures, hermetic, network-mocked).

## Open decisions (resolve at the start of the v2 session)

1. **Client form factor** — responsive web/PWA served by the Python backend (reuses the brain,
   one codebase) vs native mobile.  *(asked 2026-06-22)*
2. **Who runs the scheduled scan** — stays in GitHub Actions (free, runs when devices are off;
   v2 = interactive layer) vs v2 owns scheduling (needs an always-on host).  *(asked)*
3. **Any public surface** — fully private (only your devices/tailnet) vs keep a public read-only
   dashboard alongside the private app.  *(asked)*
4. Storage = SQLite assumed (above) — confirm, and confirm single-user.
