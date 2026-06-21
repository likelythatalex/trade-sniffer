# ROADMAP

**Single source of truth for the prioritized backlog** — what to build next, in what order,
and its current status. v1 has shipped (scheduled scanner, HTML dashboard, Discord notify,
516-ticker universe, gh-pages publishing).

This file owns *priority, sequencing, and status*. It does **not** duplicate design detail:

| For this, look here | |
|---|---|
| Design rationale for a future feature | `SPEC.md` §12 (Future phases) |
| How a *concept* is implemented today + its status | `docs/appendix.md` |
| VSA signal definitions / thresholds to calibrate | `docs/wyckoff_methodology.md` |

When an item below is built, flip its status here **and** update the matching `appendix.md`
entry in the same change (per the CLAUDE.md definition-of-done). New ideas that don't fit an
existing SPEC §12 entry get added here first; promote to SPEC only if they need real design.

Status: `TODO` (not started) · `IN PROGRESS` · `DONE` · `BLOCKED`. Items are listed in
recommended execution order within each tier.

---

## Tier 1 — Operational & correctness (near-term, cheap, high ROI)

Small fixes that tighten how the live scanner runs. These are mostly *new* todos not covered
elsewhere, so their detail lives here.

| Item | Status | Notes / detail |
|---|---|---|
| **Batch fetching** (`yf.download` multi-symbol) | DONE | `data.fetch_many` does one threaded batch (OHLCV + splits via `actions=True`); cache hits skip the network, misses are downloaded. Exchange is now resolved lazily (`resolve_exchange`) only for flagged tickers, not all 516. |
| **Drop the partial in-session bar** | DONE | `data._drop_incomplete_last_bar` drops the trailing bar when its period hasn't closed (conservative 21:00-UTC cutoff, no tzdata dep). Off-schedule runs stay no-lookahead. (The strategy-level bar-N/bar-N+1 acceptance test already exists — `test_no_lookahead_evaluation_unaffected_by_future_bars`.) |
| **`index.html` landing page on gh-pages** | DONE | `report.write_index_page` writes `output/index.html` linking `latest_daily.html` + `latest_weekly.html`; written every run so the bare Pages URL resolves. |
| **Investigate the ~9 skipped tickers** | DONE | Investigated: **not** symbol mismatches — all 10 are data-quality exclusions of *real* large moves (CNC −40% earnings, WBD +29% M&A, CHRW/EXPD earnings, EA merger-arb ATR compression). Skip/error reasons now surfaced in the run summary (consolidated log) + on the dashboard. Follow-on below. |
| **Tune data-quality to not exclude real moves** | DONE | Added a volume discriminator (`real_move_volume_mult`): a large range/gap bar on ≥ N× trailing-median volume is a real move (earnings/M&A), exempt from the spike/split exclusions — glitches don't come with real volume. Full-universe skips dropped **10 → 0** (real moves now scored). SPEC §5.2. |

## Tier 2 — Sharpen the signal (finish the PARTIAL pieces)

Completes scoring inputs that currently **abstain**, so composite scores discriminate better.
Detail/status per concept lives in `appendix.md`; definitions in `wyckoff_methodology.md`.

| Item | Status | Detail |
|---|---|---|
| **RS-vs-SPY confirmation** | DONE | SPY batch-fetched per timeframe (`scanner._benchmark_close`), passed via `StrategyContext.benchmark_close`; `wyckoff._relative_strength` scores stock-minus-SPY return over `trend_lookback`, logged as `rs_vs_spy`. Fixed a tz-index mismatch between the two fetch paths in `_normalize_columns`. SPEC §7.1. |
| **Volatility contraction ("the coil")** | DONE | `wyckoff.score_vol_contraction` compares recent bar-range vs earlier in the range (`vol_contraction_window` tunable); direction from range location. Feeds `confirmation`, logged as `vol_contraction`. SPEC §7.2. |
| **Complete spring/upthrust** | DONE | Break+snapback is the gate; magnitude scales from `SPRING_BASE_FRACTION` to full via rejection wick (`spring_wick_pct`) + above-median volume on the false-break bar. SPEC §6.3. |
| **Climax reaction check** | DONE | `wyckoff._score_climax` now requires a volume spike at an extreme **and** a subsequent ≥ `climax_reaction_atr` × ATR reaction; a bare spike abstains. SPEC §6.2; methodology §2.3. (`volume_pctile` alternative still deferred.) |
| **Calendar-based missing-bar detection** | DONE | `pandas-market-calendars` (NYSE); `data.py` computes expected sessions (once per batch) and passes them into the pure `data_quality.clean`, which flags missing sessions, forward-fills one isolated bar, and measures completeness vs the calendar (daily). SPEC §5.2. |

## Tier 3 — Validation & calibration (does the score actually work?)

| Item | Status | Detail |
|---|---|---|
| **Backtesting harness** | DONE (Phase 1: replay) | `src/backtest/` (own CLI, off the cron path). Re-scores history with the production pipeline as-of each bar, computes forward + excess-vs-SPY returns, and reports IC / by-bucket returns / hit-rate lift / per-sub-score IC. **Replay carries survivorship bias** (caveated in every report); MTF not replayed. Phase 2 (unbiased) = run the same `outcomes`/`metrics` over accumulated live `signals.csv` once it grows. SPEC §12. |
| **Calibrate seed thresholds & weights** | TODO (unblocked) | Tune the `[TUNABLE]` params using the backtester's IC / sub-score-IC / bucket monotonicity. appendix §D lists every tunable; methodology has the `[VERIFY]`/`[TUNABLE]` stubs. Best done once more live data accrues (replay is biased). |
| **Failed→revived event study (MFE)** | TODO (data-gated) | Path-dependent question: did setups that *failed then re-qualified* eventually fulfil? Needs an MFE / time-to-target outcome metric — fixed-horizon IC can't see a dip-then-trigger. **Reconstructable offline from existing `transition` + `run_ts`; no schema change.** Separate analysis from the core IC harness. Don't condition the core backtest on `transition` (selection bias). |
| **Plan-outcome simulator + policy sweep** | TODO (data-gated) | The enabler for *tuning/training the planner*. (1) A path-dependent outcome evaluator: from a plan's entry bar, walk forward and decide stop-vs-target-first → realized R (shares the MFE machinery above). (2) Run `plan_trade` over replay/live signals with a given `TradePlanConfig`, score the R distribution; sweep the config (esp. `stop_method`/`max_stop_pct`) to compare policies. **Discipline:** few params, out-of-sample/walk-forward, prefer robust plateaus over peaks, and remember replay is survivorship-biased (use the live journal for absolute expectancy). The planner being pure-on-config is what makes the A/B fair. |

## Tier 4 — Outputs & UX

| Item | Status | Detail |
|---|---|---|
| **Lightweight Charts annotations** | DONE | Dashboard rebuilt as a single shared annotated chart + ranked candidate list, using TradingView's open-source Lightweight Charts™ fed by OHLCV embedded in the page (no view-time data fetch). Annotations: range high/low band + spring/upthrust marker. Keeps an "open in TradingView" link + attribution. Superseded the display-only embed widget. SPEC §8.1, §12. |
| **Agent reviewer** | DONE (v1: bounded) | `src/review.py` — proactive, objective due-diligence pass on NEWLY-flagged setups, precomputed at scan time (Anthropic REST via `requests`, no SDK dep), baked into the dashboard. Strategy-agnostic (consumes the card contract), fail-soft, framed as review-aid-not-advice. Cost controls: off by default, NEW-only, per-run cap, cheap model, bounded output, cached by `timeframe:ticker`. Future: tool-using (deeper) agency. SPEC §8.5. |
| **Discord static chart PNG preview** | TODO | Glanceable image attached to the notification (the dashboard stays the inspection surface). SPEC §12. |
| **Dashboard: toggle to view recently failed setups** | TODO | Review/reflect affordance — show recently *invalidated* setups, not just current qualifiers. Scanner already classifies `failed`; surface those cards behind a UI toggle. Optionally feed the reviewer the setup's `transition`/episode history so a re-flagged setup is reviewed in context (the agent currently sees only the as-of snapshot). |

## Trade layer (planner + local journal) — COMPLETE

A second seam parallel to `Strategy` (signal-agnostic): turn a signal into an actionable but
**never-executed** plan, journal the trades you'd take (locally/privately), and reflect on
closed ones. Design + decisions: **SPEC §8A**. Loop: `signal → plan → journal → outcome →
reflect` — now closed end to end. All steps tested + green; built incrementally.

| Step | Status | Detail |
|---|---|---|
| 0. Disclaimer + privacy scaffolding | DONE | README + dashboard-footer disclaimer; `.gitignore` covers the private journal so trades can never leak to the public repo/gh-pages. |
| 1. Formalize `StrategyResult.levels` | DONE | Typed `Levels` (range high/low + spring_low/upthrust_high false-break extremes) on `StrategyResult`; wyckoff populates the *facts*, the combiner propagates the direction-driving strategy's levels onto the composite, the planner derives entry/stop/target from them. Facts-in-strategy / policy-in-planner keeps the planner strategy-agnostic. |
| 2. `trade_plan.py` (pure, public) | DONE | `TradePlan` + `plan_trade(direction, levels, cfg)`: entry (confirmation break), stop (structural + % buffer), target (measured move), sizing (account-risk %, 1%/$100k notional), management playbook (BE@+1R, scale 50% @ target, trail N×ATR). Flat `trade_plan:` config block + fail-fast validation; abstains (`None`) on degenerate setups. `[TUNABLE]` seeds. SPEC §8A.1. |
| 3. Dashboard render of the plan | DONE | `scanner._plan_data` attaches the suggested plan to each flagged card; the template shows entry/stop/target/R:R/size + the management playbook and draws entry/stop/target as solid price lines on the chart (range band stays dashed). Display-only, public (no personal data). |
| 4. `journal.py` + CLI (PRIVATE) | DONE | Local-only, gitignored (`journal.csv`), never in CI. `add` / `list` / `close` trades via `python -m src.journal …`; tolerant CSV store rewritten on mutate, direction aliases + fail-fast validation. Data layer only — outcomes/AI are steps 5-6. SPEC §8A.2. |
| 5. Journal auto-outcome | DONE | Shared pure `trade_outcome.py` (path-dependent: stop-vs-target-first, realized R, MFE/MAE; conservative both-in-one-bar tie-break, forward-bars-only). `journal evaluate_entries` (prices injected) + `journal report` CLI (fetches daily). Outcomes derived, not stored. Same evaluator the Tier-3 policy sweep will reuse. SPEC §8A.2. |
| 6. Post-trade agent review (PRIVATE) | DONE | `journal review`: reuses `AnthropicReviewer` with a reflection rubric (judges PROCESS vs OUTCOME; verdict good/mixed/poor) on CLOSED trades. `review_closed_trades` (stub-injected) is closed-only, per-run capped, cached in gitignored `trade_reviews.json`; needs the API key (no enabled gate — explicit command), fail-soft, never advice. SPEC §8A.2. |

## Hosting & UI evolution (phased)

Two orthogonal axes decide this: **public vs private** (the journal is private trades → can't
live on the public gh-pages) and **static vs dynamic** (a backend is only needed for
*view-time* compute/mutation). Most "more features" wants need **private hosting, not a
backend**. The codebase is structured (pure functions, per-concern CLIs, `Strategy`/`Reviewer`
seams, separate templates) so the eventual backend is a **packaging change, not a rewrite** —
routes just call the existing functions.

| Phase | Status | What | Trigger to start |
|---|---|---|---|
| A. Private read-only journal view | DONE | `journal html` renders trades + outcomes + reflections + summary stats to a gitignored `journal_report.html`. Host privately by running it locally and reaching it over **Tailscale** (zero-cost, nothing public, tailnet = auth). | wanting to glance at the journal away from the desk |
| B. Small web app (the backend step) | TODO | FastAPI/Flask wrapping the existing functions: dashboard + journal CRUD + on-demand scan; SQLite for journal/signals; simple auth. Host on Fly.io/Render/$5 VPS — or still local behind Tailscale/Cloudflare Tunnel to stay free + private. | a CONCRETE need: add/close trades from phone, on-demand re-scan, or `signals.csv` outgrowing a flat file |
| C. Scale | TODO | Postgres, background workers (heavy backtests / policy sweeps), multi-device. | only if B strains |

Keep the **scanner** static + public (gh-pages) — it's the correct design for it. Don't build
B speculatively; name the trigger first (YAGNI).

## Market context & macro monitoring (future subsystem)

A layer **distinct from the per-ticker `Strategy` seam**: strategies ask "is THIS stock setting
up?"; market context asks "what's the environment?" — computed **once per run, shared across all
tickers**, so it is *not* a combiner strategy and can't be calibrated the per-ticker way. It is
applied at the **composite/report layer** (annotate → later scale → maybe gate), never inside a
per-ticker score. Design it so each macro input is "a fetch + a reading," the way a strategy is
"a file + a config block" (a `market_data.py` fetch + a pure `market_context.py` reading).

| Phase | Status | What |
|---|---|---|
| 1. Regime + breadth | TODO (next) | SPY trend (vs 200-dma) + **breadth** (% of the scanned universe above its 200-dma — nearly free; we already scan everyone). Emit a `MarketContext`; first **annotate + log** it (dashboard + a market column), then **scale** conviction (risk-off down-weights longs), and only maybe **gate**. |
| 2. Macro / intermarket | TODO | Rates + rates-vol (`^TNX` / TLT / MOVE), credit risk appetite (HYG/JNK vs IEF — junk-bond spreads), intermarket ratios (IWM small-caps vs QQQ growth, value/growth, defensives/cyclicals), sector rotation/correlation. Most free via yfinance ETFs; MOVE needs a source check. |
| 3. Cycle positioning | TODO | "Where are we in the cycle" — partly **reuse the Wyckoff engine on SPY/sector ETFs** (index-level accumulation/markup/distribution/markdown = Wyckoff applied to the market) plus the Phase-2 intermarket ratios. |

Discipline: market context is shared across all tickers, so it can't be validated the per-ticker
way — ship it as **displayed context first**, add scaling/gating only once its value shows.

| Insider transactions (strategy) | DONE | Independent, non-price signal (smart-money disclosures) — the literal version of Wyckoff's "composite operator". `strategies/insider.py` + `insider_data.py` (`EdgarInsiderSource`: ticker→CIK + Form 4 parse, day-cached, fail-soft, SEC UA). Relative net-buy/sell ratio scoring with `sell_weight`; no-lookahead on the **filing** date. Weight 0; logged as `insider_score` (schema v5). **Backtestable** (EDGAR history) — unlike sentiment. Future: Finnhub source, role/size/cluster weighting, calibration. SPEC §6/§12. |

## Tier 5 — Future phases & architecture

Bigger directions, all with design rationale in **SPEC §12** — see there for the "why". Listed
here only for visibility/sequencing.

| Item | Status | Detail |
|---|---|---|
| Additional strategies (momentum / RS / volatility / sentiment) | IN PROGRESS | **Momentum DONE** (`strategies/momentum.py`: trend regime + ROC). **News sentiment DONE** (`strategies/news_sentiment.py` + `sentiment.py` + `sentiment_data.py`: pluggable NewsSource/SentimentScorer, v1 = yfinance headlines + VADER, whole-universe, no-lookahead, day-cached). Both at **weight 0** (logged as `momentum_score` / `news_sentiment_score`, inert) until calibration. News sentiment is **forward-only** (not backtestable). SPEC §6, §12. |
| News-sentiment scorer/source upgrades | TODO | Swap the v1 VADER lexicon for a finance-aware engine — local **Ollama LLM** (free/private via the GPU) or **FinBERT** — and/or a richer `NewsSource` (Finnhub/StockTwits). Pluggable seams already in place; data-gated on whether v1 coverage proves useful. SPEC §12. |
| Social-sentiment strategy (Reddit/StockTwits) | TODO | A *separate*, independent crowd-sentiment strategy (`social_sentiment`) stacked alongside news sentiment. Needs a legit API (no scraping); VADER is actually well-suited to social text. Higher volume → where the local LLM earns its keep. SPEC §12. |
| Confirmation stacking | TODO (unblocked) | Now possible: two strategies exist + the combiner aggregates them. First needs momentum's weight calibrated from accrued `momentum_score` vs outcomes (data-gated), *then* the correlation-aware weighting below. SPEC §12. |
| Correlation-awareness in `combiner.py` | TODO | Down-weight correlated strategy clusters (why per-strategy scores are logged). SPEC §12; appendix "Signal Correlation Awareness" (FUTURE). |
| Crypto mode | TODO | Separate profile (24/7, crypto RS benchmark, crypto symbols/liquidity). Same phase as multi-strategy. SPEC §12. |
| Telegram notification channel | TODO | Behind the existing `notify.py` interface. SPEC §8.3, §12. |
| Regime-aware feature baselines | TODO | e.g. reset volatility baseline after earnings expansion. SPEC §12. |
| Climax-anchored range boundaries | TODO | Refinement over the v1 support/resistance band — the book's true Creek/ICE (anchored on the AR). SPEC §12; methodology §2.3/§3. |
| Cause & Effect target projection (P&F count) | TODO | The trade planner's target is currently a range/ATR measured-move; Wyckoff's third law sizes the *effect* from the *cause* via a Point-and-Figure horizontal count. Add a P&F (or range-height) cause-based target as an alternative `stop_method`-style option in `trade_plan.py`. Honest gap, flagged in methodology §0 / appendix "Law of Cause & Effect". |
| Paid data source (Polygon) | TODO | If intraday / higher quality is ever needed. SPEC §12. |
| IBKR optional output | TODO | For users keeping TWS running. SPEC §12. |
| Hosted multi-tool dashboard / orchestrator | TODO (trigger-based) | Phased plan lives in **Hosting & UI evolution** above (Phase A done; B = the backend step, trigger-gated). The static gh-pages + GitHub Actions model is the orchestrator until a concrete trigger fires. |
