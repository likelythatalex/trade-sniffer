# Strategies — overview & shared conventions

The connective tissue for every signal in this scanner: the one OOP seam they plug into, the
conventions they *all* obey, and how their scores combine. Read this first; then read a
strategy's own module docstring for its specific rules. Wyckoff (the only weighted strategy)
also has a full theory→rules bridge in [wyckoff_methodology.md](wyckoff_methodology.md); the
per-concept "how it's implemented + status" entries live in [appendix.md](appendix.md).

> **Why one overview instead of a file per strategy.** Momentum, news-sentiment, and insider
> are each a *single-formula* strategy whose module docstring already explains the model in
> full. A separate methodology doc per strategy would duplicate that docstring + the appendix
> entry and rot out of sync. The value that *wasn't* written down is the shared contract — so
> that's what this doc captures. (See [ROADMAP.md](../ROADMAP.md) for what's planned.)

---

## The one seam: `Strategy`

Everything analysis-related is plain functions + dataclasses **except** this one abstraction,
which earns its keep ([base.py](../src/strategies/base.py), SPEC §6):

```python
class Strategy(ABC):
    name: str                                           # registry key == config key
    def evaluate(self, df, context: StrategyContext) -> StrategyResult: ...
```

- **`StrategyContext`** carries everything a strategy needs beyond raw OHLCV — the precomputed
  relative `features`, the resolved per-timeframe `params`, the other timeframe's `prior_state`
  (MTF cross-read), and any upstream-fetched inputs (`benchmark_close`, `headlines`,
  `insider_transactions`). Strategies are **pure**: they never read config, re-derive
  normalization, or do I/O. The scanner fetches; the strategy scores.
- **`StrategyResult`** is the normalized output every strategy emits on a common scale, so
  [combiner.py](../src/combiner.py) can aggregate any mix without special-casing.
- **`registry.py`** maps `name → class`, discovered from config. Adding a strategy is **"a new
  file in `src/strategies/` + a block under `strategies:` in `config.yaml`"** — the scanner
  engine never hardcodes which strategies exist.

---

## Conventions every strategy follows

These are the invariants the combiner, the calibration log, and future correlation work all
rely on. Break one and you break those downstream — so they're documented here once.

| Convention | Rule | Why |
|---|---|---|
| **Signed score** | Each strategy computes a *signed* composite in `[-100, +100]` (**+ = accumulation/bullish, − = distribution/bearish**). `StrategyResult.score` is its **magnitude** (conviction); `direction` is its **sign** (gated by a `DIRECTION_FLOOR ≈ 10`, below which → `"none"`). The raw signed value is kept in `metadata["signed"]` for logging. | One scale lets independent signals stack; magnitude vs. sign cleanly separates "how sure" from "which way". |
| **Pure + injected data** | No network/file I/O inside a strategy. External inputs (SPY, headlines, Form 4s) are fetched **once per run** by the scanner and passed via `StrategyContext`. | Testable + backtestable; mirrors the SPY/`benchmark_close` pattern. |
| **Abstain ≠ neutral** | "No data" → `signed = None` (logged as `""`); "data, no lean" → `0.0`. NaN features make a signal simply *not fire*, never propagate. | Calibration must tell *absent* from *neutral*; conflating them poisons the dataset. The composite is always finite (SPEC §6.4). |
| **Relative thresholds only** | Every "high/low/narrow/wide" call is defined against the stock's *own* rolling distribution or a self-normalizing ratio — **never an absolute dollar/share threshold**. (The liquidity gate's absolute floor is the one intentional exception, and it isn't a strategy.) | Absolute thresholds are meaningless across a universe. |
| **No lookahead** | Only data available at the evaluated bar's close. Strategies with external data apply their own **as-of cutoff** (news: publish date; insider: *filing* date). | Honest signals; pre-validates the backtester. |
| **Weight-0 "logged but inert"** | A new strategy ships at `weight: 0`: it computes a score and logs it to `signals.csv` every run, but contributes **nothing** to the composite (weight×score = 0, and it can't win the direction tie-break) until calibration earns it a weight. | Accrue the independence/forward-value data *before* trusting the signal. |

---

## How scores combine

[`combiner.combine`](../src/combiner.py) (the single, designated home for all cross-strategy
logic) takes `{name: StrategyResult}` + `{name: weight}` and returns one composite:

- **Score** = weighted average of the per-strategy conviction scores.
- **Direction** = the strongest *directional* contributor by `weight × score` (so weight-0
  strategies can never set it — they're truly inert).
- **Sub-scores** are namespaced `strategy.subscore` so adding a strategy *extends* the
  breakdown instead of colliding. The winning contributor's structural `levels` ride along, so
  the trade planner reads `composite.levels` without knowing which strategy produced them.

**Correlation-awareness is deliberately future work** and belongs *here* when built: stacking
only adds information if the signals are independent (three trend-flavored signals agreeing is
one signal counted thrice). The data to measure pairwise correlation is being logged now; the
down-weighting logic will live in `combiner.py`. See appendix → *Signal Correlation Awareness*.

---

## The strategies

| Strategy | Reads | Independence from price | Backtestable? | Weight | Code | Detail |
|---|---|---|---|---|---|---|
| **wyckoff** | Range + volume behavior + spring/UTAD over relative features | — (it *is* the price-structure read) | Replay (survivorship-biased) | **weighted** | [wyckoff.py](../src/strategies/wyckoff.py) | [wyckoff_methodology.md](wyckoff_methodology.md) |
| **momentum** | Trend regime (price vs MA) + rate-of-change | Low — trend-flavored, correlates with price | Yes (price-only) | 0 (inert) | [momentum.py](../src/strategies/momentum.py) | appendix → *Conviction Score / Confirmation Stacking* |
| **news_sentiment** | Recent headline polarity (VADER lexicon) | **High** — non-price | **No** — forward-only (no free historical news) | 0 (inert) | [news_sentiment.py](../src/strategies/news_sentiment.py) + [sentiment.py](../src/sentiment.py) / [sentiment_data.py](../src/sentiment_data.py) | appendix → *News Sentiment* |
| **insider** | Net open-market Form 4 buying vs selling (EDGAR) | **High** — non-price | **Yes** — EDGAR keeps history; cutoff = filing date | 0 (inert) | [insider.py](../src/strategies/insider.py) + [insider_data.py](../src/insider_data.py) | appendix → *Insider Transactions* |

The two **non-price** signals (sentiment, insider) are the most independent of the price-based
trio, so they're the strongest stacking candidates *if* the accrued data bears it out — which
is exactly why they're logged-but-inert rather than guessed-at-a-weight.

### Not a strategy: market context

[`market_context.py`](../src/market_context.py) (regime + breadth) is a **market-wide,
once-per-run** layer, *not* a per-ticker `Strategy` — it asks "what's the weather?", not "is
this stock setting up?". It lives outside this seam, logs to its own `market.csv`, and is
displayed-only (not yet applied to scores, because a market-wide reading can't be calibrated
the per-ticker way). Kept here only to prevent confusion; see appendix → *Market Context*.

---

## Adding a strategy (the checklist)

1. New file in `src/strategies/` implementing `Strategy.evaluate` → a signed `StrategyResult`,
   obeying every convention in the table above (pure, signed, abstain≠neutral, relative, no
   lookahead).
2. Register it in [registry.py](../src/strategies/registry.py).
3. Add a `strategies:` block in `config.yaml` (**start at `weight: 0`**) + any per-timeframe
   params; wire resolution in `config.resolve_strategy_params`.
4. If it needs external data, fetch it **upstream in the scanner** (whole-universe, day-cached,
   fail-soft) and pass it through `StrategyContext` — never fetch inside the strategy.
5. Log its signed score to `signals.csv` (schema bump + migrate; see SPEC §8.4).
6. Unit test with a hand-checked fixture; add an appendix entry (status + "how it's implemented"
   + module ref) and a row in the table above.
