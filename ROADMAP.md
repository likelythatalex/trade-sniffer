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
| **Drop the partial in-session bar** | DONE | `data._drop_incomplete_last_bar` drops the trailing bar when its period hasn't closed (conservative 21:00-UTC cutoff, no tzdata dep). Off-schedule runs stay no-lookahead. Strategy-level bar-N/bar-N+1 acceptance test (SPEC §11) still pending → see "Lookahead Bias" in `appendix.md`. |
| **`index.html` landing page on gh-pages** | DONE | `report.write_index_page` writes `output/index.html` linking `latest_daily.html` + `latest_weekly.html`; written every run so the bare Pages URL resolves. |
| **Investigate the ~9 skipped tickers** | TODO | Likely Wikipedia↔yfinance symbol mismatches → fix via `symbol_overrides.csv` or normalization in `scripts/build_universe.py`. Also surface a skipped/errored list in the run summary (today it's only in logs; SPEC §10 already specs the counts). |

## Tier 2 — Sharpen the signal (finish the PARTIAL pieces)

Completes scoring inputs that currently **abstain**, so composite scores discriminate better.
Detail/status per concept lives in `appendix.md`; definitions in `wyckoff_methodology.md`.

| Item | Status | Detail |
|---|---|---|
| **RS-vs-SPY confirmation** | DONE | SPY batch-fetched per timeframe (`scanner._benchmark_close`), passed via `StrategyContext.benchmark_close`; `wyckoff._relative_strength` scores stock-minus-SPY return over `trend_lookback`, logged as `rs_vs_spy`. Fixed a tz-index mismatch between the two fetch paths in `_normalize_columns`. SPEC §7.1. |
| **Volatility contraction ("the coil")** | DONE | `wyckoff.score_vol_contraction` compares recent bar-range vs earlier in the range (`vol_contraction_window` tunable); direction from range location. Feeds `confirmation`, logged as `vol_contraction`. SPEC §7.2. |
| **Complete spring/upthrust** | DONE | Break+snapback is the gate; magnitude scales from `SPRING_BASE_FRACTION` to full via rejection wick (`spring_wick_pct`) + above-median volume on the false-break bar. SPEC §6.3. |
| **Climax reaction check** | DONE | `wyckoff._score_climax` now requires a volume spike at an extreme **and** a subsequent ≥ `climax_reaction_atr` × ATR reaction; a bare spike abstains. SPEC §6.2; methodology §2.3. (`volume_pctile` alternative still deferred.) |
| **Calendar-based missing-bar detection** | TODO | Needs a market calendar; deferred in v1. appendix "Data Quality" (PARTIAL); SPEC §5.2. |

## Tier 3 — Validation & calibration (does the score actually work?)

| Item | Status | Detail |
|---|---|---|
| **Backtesting harness** | TODO | Replay `signals.csv` forward N bars: did high scores precede markup/markdown? **Must** use a point-in-time universe (survivorship bias, SPEC §4.1). Highest long-term value. SPEC §12. |
| **Calibrate seed thresholds & weights** | TODO | Tune the `[TUNABLE]` params against accumulated `signals.csv`. Depends on the backtester + data accumulating first. appendix §D lists every tunable; methodology has the `[VERIFY]`/`[TUNABLE]` stubs. |

## Tier 4 — Outputs & UX

| Item | Status | Detail |
|---|---|---|
| **Lightweight Charts annotations** | TODO | Render charts ourselves (range lines, spring/upthrust markers, score labels) using TradingView's open-source Lightweight Charts™, superseding the display-only embed widget. SPEC §12. |
| **Discord static chart PNG preview** | TODO | Glanceable image attached to the notification (the dashboard stays the inspection surface). SPEC §12. |

## Tier 5 — Future phases & architecture

Bigger directions, all with design rationale in **SPEC §12** — see there for the "why". Listed
here only for visibility/sequencing.

| Item | Status | Detail |
|---|---|---|
| Additional strategies (momentum / RS / volatility) | TODO | Each = new file in `strategies/` + config line. SPEC §6, §12. |
| Confirmation stacking | TODO | Combiner raises conviction when independent strategies agree. SPEC §12. |
| Correlation-awareness in `combiner.py` | TODO | Down-weight correlated strategy clusters (why per-strategy scores are logged). SPEC §12; appendix "Signal Correlation Awareness" (FUTURE). |
| Crypto mode | TODO | Separate profile (24/7, crypto RS benchmark, crypto symbols/liquidity). Same phase as multi-strategy. SPEC §12. |
| Telegram notification channel | TODO | Behind the existing `notify.py` interface. SPEC §8.3, §12. |
| Regime-aware feature baselines | TODO | e.g. reset volatility baseline after earnings expansion. SPEC §12. |
| Climax-anchored range boundaries | TODO | Refinement over the v1 support/resistance band. SPEC §12; methodology §2.3/§3. |
| Paid data source (Polygon) | TODO | If intraday / higher quality is ever needed. SPEC §12. |
| IBKR optional output | TODO | For users keeping TWS running. SPEC §12. |
