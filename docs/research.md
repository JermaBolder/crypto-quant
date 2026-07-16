# The research story: three chapters, three honest verdicts

This repo ran three quantitative studies on public data with the same harness
and the same discipline. One found nothing and said so. One found something
real — and honestly reported that the clever part of it doesn't work. One
checked our numbers against the live institutional implementation of the same
trade — and found the interesting gap pointing the *other* way.

| chapter | question | verdict | bottom line |
|---|---|---|---|
| ML on 1m order flow | can features of the last hour predict the next 5–60 min, net of costs? | **NO EDGE** | best config **−13.1 bps/bet** after costs |
| Funding carry | is the perp funding stream harvestable, and does timing beat always-on? | **HARVEST YES, TIMING NO** | always-on **+11.9%/yr** net; best timing rule loses to it OOS |
| Ethena vs our carry | does sUSDe earn what our measured funding predicts? | **STRUCTURE, NOT ALPHA** | realized **10.0%/yr** vs predicted 16.6–17.3; same regime, compressed amplitude |

All three verdicts were produced by rules committed *before* the numbers were
computed. That's the actual subject of this document.

## The method

Four decisions did all the work. None of them is exotic; all of them are
routinely skipped.

**1. Money is the only metric.** Accuracy is a vanity metric: a 3-class
predictor on imbalanced data can post 60%+ hit rates and still lose money.
Every idea here — model, rule, baseline — is scored the same way: net return
after costs, per bet or per interval, with totals. The evaluation harness
(`evaluate.py`, `carry_eval.py`) prints money, not F1.

**2. Baselines first, and baselines are in money too.** Before any model runs,
trivial strategies set the bar: always-long, random direction, sign-of-imbalance,
sign-of-momentum (chapter 1); always-on harvest, random 50%, hold-when-funding-positive
(chapter 2). If a model can't beat *these* on the same out-of-sample rows, it has
found nothing — whatever its validation loss says.

**3. Time is respected.** Labels look forward, so naive cross-validation leaks:
the last `H` rows of a train fold carry labels computed from test-window prices.
`purged_splits` cuts those rows off every train fold (walk-forward, expanding,
5 folds). Any tunable knob (abstain threshold τ, entry threshold θ) is picked
*inside* the train fold on a nested time-ordered split — never on test. Features
use only data at or before *t*; each dataset has exactly one look-forward column,
and the tests pin that.

**4. Stop rules are pre-committed.** Written into the module docstring AND
enforced in `main()` before the first run: *"best config OOS net ≤ 0 ⇒ iteration
closed, no further tuning."* Without this, a negative result is just an
invitation to keep tuning until noise produces a false positive. With ~20
configurations and 5 folds each, that false positive is a matter of time, not
skill.

One more habit that mattered: **data gotchas are verified, not assumed.** Spot
aggTrades dumps stamp in microseconds, the live websocket in milliseconds, the
futures dumps in milliseconds *with jitter*; futures monthly CSVs have header
rows, spot daily ones don't. Every one of these was checked against a real file
and then pinned by a unit test — because a silent ×1000 timestamp error would
poison every result downstream.

## Chapter 1 — ML on 1-minute order flow: NO EDGE

**Setup.** 1-minute order-flow bars from `agg_trades` (aggressor side known),
label = sign of the forward H-bar return with a volatility-scaled dead zone
(`thr = cost + k·σ_local·√H`) so the class mix stays comparable across regimes
and sub-cost moves are never a target. Cost model: ~15 bps round trip per
directional bet, charged once per bet.

**v1** (14 days, 9 features, logistic regression): OOS hit 48.3%, net
**−16.3 bps/bet**. Could be dismissed as too little data — so v2 fixed that.

**v2** (90 days / 80.3M trades, 22 features across returns/momentum, vol regime,
bar geometry, order-flow imbalance ratios, trade-size structure, VWAP distance,
clock; logreg + gradient boosting; abstain threshold picked inside train;
purged walk-forward): **every configuration negative, 0/5 positive folds
everywhere.** Best: HGB at H=60m, hit 51.8%, net **−13.1 bps/bet**.

The interesting part: the model *does* find signal — roughly **+2 bps gross**
per bet, statistically real. Against ~15 bps of round-trip cost it is worthless:
**predictability without tradability**. That phrase is the chapter's actual
finding. Weak public-data signals on 1m BTC exist and do not survive costs.
Verified twice, closed by the stop rule, no further tuning.

## Chapter 2 — Funding carry: HARVEST YES, TIMING NO

**Mechanism.** Every 8h, Binance USDT-M perps transfer funding between longs and
shorts. Short perp + long spot is delta-neutral and collects the payment when
funding is positive; the premium index (the basis itself, in return units) marks
against the short leg. Per-interval realized carry:
`carry = f_next − Δpremium` — with `f_next` and the next premium the only
look-forwards (the rate settled at the next boundary accrues over the interval
you hold; it is not known when you decide).

**The cost model is the whole story.** Costs are charged on **turnover** — 14
bps per unit position change (taker, both legs, one way; 28 bps round trip) —
not per interval. A position held 90 intervals pays the round trip once. This is
the structural difference from chapter 1, where every bet paid full freight.

**Data.** 2020-01 … 2026-06, 7,089 8h intervals. Funding: mean +1.09 bps/8h,
median +0.96 (≈ the 1 bp default rate), 85.6% of intervals positive,
autocorr(1) = 0.80 — strongly persistent, which is exactly why timing it looks
tempting.

**Results.**

- **Always-on harvest: net +1.09 bps/8h ≈ +11.9%/yr** after full retail costs.
  One episode in 6.5 years; cost is a rounding error.
- **Timing rules** (enter when funding — or its 3-interval mean — exceeds θ; θ
  picked inside train folds; purged walk-forward; OOS only): best nets
  **+0.31 bps/8h vs always-on's +0.92 on the same OOS rows.** Not eligible to win.
- The "obvious" filter — hold only while funding is positive — outright
  **loses** (−0.83 bps/8h held): it enters and exits 481 times, and 481 × 28 bps
  of fees dwarf the negative intervals it dodges. Random 50% is a bloodbath
  (−70.8%/yr) for the same reason. **Every filter pays more in fees than it
  saves in negative funding.**
- Sensitivity at VIP/maker-grade fees (5 bps per leg change) narrows nothing:
  the best rule still trails always-on.

Stop rule 2 closed the chapter: **the carry exists; timing it doesn't.** The
honest product of this chapter is a parameter-free strategy any spreadsheet
could have found — plus the demonstration, with real numbers, of why everything
smarter than it fails.

## Chapter 3 — Ethena vs our carry: STRUCTURE, NOT ALPHA

**Question.** sUSDe is the institutional implementation of exactly chapter 2's
strategy — short perp, long collateral, harvest funding — and Ethena publishes
what it actually paid stakers (DefiLlama archives it daily). Over the same
window our measured BTC funding annualizes to 6.79% gross while sUSDe realized
≈10%. Candidate explanations declared up front: (a) hotter hedges than BTC,
(b) multi-exchange capture, (c) concentration — yield accrues on *all* of
USDe's backing but is paid only to the staked share.

**Method.** This chapter is an *attribution* study: nothing is tuned or fit,
so there is no OOS split — what replaces the stop rule is a **closure rule**,
committed before the numbers ran: measure candidates (a) and (c) — the two
that free data can measure — report where realized APY falls against the
prediction, and report the residual with its candidate owners instead of
chasing it with more ingestion.

**Data.** 866 joint days (2024-02-16 … 2026-06-30): daily sUSDe APY + staked
TVL, USDe circulating supply, Binance BTC and ETH funding (8h → daily; every
day in the window had exactly 3 settlements). Realized sUSDe: mean
**10.04%/yr**, median 6.00 — the yield is regime-concentrated, not steady.

**Results.**

- **(a) Hedge choice is dead:** ETH funding 6.99%/yr gross vs BTC 6.79 —
  **+0.20 pp**. On this window there is no "hotter hedge" story, and with
  asset choice worth 0.2 pp, cross-venue spreads (b) are second-order too;
  they were never ingested, per the closure rule.
- **(c) Concentration dominates — and over-explains.** Staked share averaged
  48.8% (range 13–73%), a **2.29×** multiplier on the stakers' rate. Applied
  day by day it predicts 16.6 (BTC) – 17.3 (ETH) %/yr for a 100%-hedged
  backing. Realized 10.04 sits **6.6 pp below the band's floor.**
- So the teaser's question inverts. The puzzle was never "why does Ethena earn
  more than raw funding" — counting the multiplier, it's "why do stakers get
  *less* than levered funding". The answer is visible in the shape: the 7-day
  correlation between sUSDe APY and the prediction is **+0.83** (same regime,
  tightly tracked) but the amplitude is compressed *both ways* — 2024Q1
  realized 32%/yr against a predicted 98; thin 2026Q1 realized 4.0 against a
  predicted 0.1. A backing that is partly liquid stables (flat, T-bill-like
  yield) plus a reserve fund acts as a damper: it caps the fat quarters and
  floors the thin ones.
- **Residual owners** (reported, not chased): the liquid-stables/LST share of
  the backing, reserve-fund policy, the actual hedge mix, venue spreads.

**What it means for chapter 2's trade.** Ethena's edge over a solo harvester
is not a better hedge — it is the same funding stream, correlation +0.83 —
it's *structure*: harvest on all backing, pay only stakers, smooth the regime.
On the same window, running chapter 2's strategy yourself grossed ≈6.8%/yr;
holding the product paid ≈10. The honest conclusion for a small account is
almost embarrassing: the efficient way to hold this trade is to hold sUSDe —
priced in a different currency, though: smart-contract, custody and depeg risk
instead of exchange margin risk, and none of that is measured here.

## Why the same harness produced opposite verdicts

Not because chapter 2's signal is stronger — funding's +1.09 bps/8h mean is
*weaker* than the ML model's +2 bps gross per bet. The difference is **how often
each strategy pays the toll.** The 1m model re-decides every bet and pays ~15
bps each time it acts; the carry position decides once and amortizes 28 bps over
six and a half years. Same fees, opposite outcomes. The tradability of a signal
is a property of its *cost structure*, not just its information content — that
is the single most transferable lesson in this repo.

## What these studies are not

Signal-quality proxies, not backtests. Deliberately ignored: slippage and
market impact, margin and liquidation risk on the short perp leg, drawdown
sizing, overlapping-position accounting (chapter 1), BTC borrow costs (which is
why the negative-funding side was excluded as not implementable rather than
modeled optimistically), fee tiers better than retail (shown only as labeled
sensitivity), and every symbol other than BTCUSDT. A positive verdict here would
mean "worth deeper validation" — never "trade this."

## Why the negative results are the point

The stop rules cost nothing to write and everything to obey. Chapter 1 was
closed at a moment when one more feature family, one more model, one more
horizon each felt *obviously* worth trying — that feeling is precisely how
p-hacking happens on financial data, where the noise floor is high, regimes
shift, and any sufficiently long search finds a config with positive OOS net by
luck. Chapter 2's stop rule cut the more subtle temptation: the harvest is real,
so surely a *clever enough* filter must improve it. Two rule families and one
sensitivity check later, the pre-committed bar said no, and the chapter closed.

A "no edge" produced this way is not a failure to find something; it is a
finding — reproducible from public data, end to end, by anyone:

```bash
.venv/bin/python backfill.py --days 90 && .venv/bin/python model.py       # chapter 1
.venv/bin/python backfill_futures.py 2020-01:2026-06 && .venv/bin/python carry_eval.py  # chapter 2
.venv/bin/python backfill_futures.py 2024-02:2026-06 --symbol ETHUSDT --funding-only \
  && .venv/bin/python backfill_ethena.py && .venv/bin/python ethena_eval.py  # chapter 3
```
