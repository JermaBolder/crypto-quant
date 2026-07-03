"""Carry chapter verdict: is the funding harvest tradable, and does timing beat
always-on? Baselines-in-money first, rules OOS second, one pre-committed rule.

STOP RULE (agreed in advance, so tuning cannot drag on):
  1. If always-on harvest (parameter-free, full sample) nets <= 0 bps/interval
     after full costs (COST_LEG per unit position change) -> no harvestable
     carry at retail fees; chapter CLOSED.
  2. Otherwise, if the best theta-rule — judged OOS-only under purged
     walk-forward, eligibility >= MIN_HELD held OOS intervals — does not beat
     always-on's net bps/interval ON THE SAME OOS ROWS -> carry exists but
     there is NO TIMING EDGE beyond always-on; chapter CLOSED, always-on
     documented as the honest result.
  3. Either way the verdict and numbers go to the README. The ML step opens
     ONLY if a rule beats always-on OOS. LOW_COST sensitivity is annotated but
     never overturns the verdict.

Costs are charged on TURNOVER, not per interval: a harvest position held 90
intervals pays the round trip once. That is the structural difference from
evaluate.py's per-bet costing and the reason carry can survive fees that killed
the 1m ML signal.

Like evaluate.py: this is a signal-quality proxy, NOT a backtest — it ignores
margin, liquidation risk on the short perp leg, slippage, and drawdowns.
"""
from __future__ import annotations

import numpy as np

from carry import build_carry
from evaluate import purged_splits

PERP_TAKER = 0.0004      # Binance USDT-M taker, regular tier — no VIP assumed
SPOT_TAKER = 0.0010      # Binance spot taker; the hedge leg is the expensive one
COST_LEG = PERP_TAKER + SPOT_TAKER   # 14 bps per UNIT position change (both legs, one way)
LOW_COST_LEG = 0.0005    # sensitivity only: VIP/BNB tiers + maker fills

IPY = 3 * 365            # 8h funding intervals per year
THETAS = (0.0, 5e-5, 1e-4, 2e-4, 3e-4, 5e-4)   # entry thresholds; 1e-4 = the 1bp default rate
MIN_HELD = 100           # OOS eligibility, mirrors model.py's bets >= 100
HOLD = 1                 # payoff reaches 1 interval ahead -> purge horizon


def turnover(pos: np.ndarray) -> float:
    """Units of position changed: initial entry + every change + final close."""
    if len(pos) == 0:
        return 0.0
    return float(abs(pos[0]) + np.abs(np.diff(pos)).sum() + abs(pos[-1]))


def episode_stats(pos: np.ndarray, carry: np.ndarray, cost_leg: float = COST_LEG) -> dict:
    pos = np.asarray(pos, dtype=float)
    held = int((pos != 0).sum())
    prev = np.concatenate([[0.0], pos[:-1]])
    episodes = int(((pos != 0) & (pos != prev)).sum())   # flat->pos entries and flips
    gross = float((pos * carry).sum())
    net = gross - cost_leg * turnover(pos)
    pnl = (pos * carry)[pos != 0]
    return {
        "held": held,
        "episodes": episodes,
        "gross": gross,
        "net": net,
        "gross_bps_held": gross / held * 1e4 if held else 0.0,
        "net_bps_held": net / held * 1e4 if held else 0.0,
        "net_bps_cal": net / len(pos) * 1e4 if len(pos) else 0.0,   # per CALENDAR interval
        "hit": float((pnl > 0).mean()) if held else float("nan"),
        "ann_pct": net * IPY / len(pos) * 100 if len(pos) else 0.0,
    }


def report_carry(name: str, pos: np.ndarray, carry: np.ndarray,
                 cost_leg: float = COST_LEG) -> dict:
    s = episode_stats(pos, carry, cost_leg)
    if s["held"] == 0:
        print(f"{name:34s} | never holds")
        return s
    print(f"{name:34s} | held {s['held']:5d} ({s['held']/len(pos):4.0%}) "
          f"| eps {s['episodes']:4d} | hit {s['hit']:5.1%} "
          f"| net {s['net_bps_held']:+5.2f} bps/8h held | {s['ann_pct']:+5.1f} %/yr "
          f"| total {s['net']*1e4:+8.0f} bps")
    return s


def pick_theta(sig: np.ndarray, carry: np.ndarray, cost_leg: float = COST_LEG) -> float:
    """Theta maximizing TOTAL net on the validation slice (train data only).
    Total (not per-held) is the honest target: a rule that is flat 99% of the
    time with one lucky interval makes no money. Starving thetas are skipped."""
    best_theta, best_net = 0.0, -np.inf
    for theta in THETAS:
        pos = (sig > theta).astype(float)
        if (pos != 0).sum() < MIN_HELD // 3:
            continue
        net = episode_stats(pos, carry, cost_leg)["net"]
        if net > best_net:
            best_net, best_theta = net, theta
    return best_theta


def run_rules(f: np.ndarray, carry: np.ndarray) -> list[dict]:
    """Theta-rules under purged walk-forward; theta picked on the last 20% of
    each train fold (the regime nearest to test), applied OOS. Always-on is
    re-scored on the SAME OOS rows — apples to apples."""
    n = len(f)
    f3 = np.convolve(f, np.ones(3) / 3, mode="full")[: n]
    f3[:2] = -np.inf                                  # rolling-mean warmup: never hold
    rules = {"rule f>theta": f, "rule mean3(f)>theta": f3}

    out = []
    for name, sig in rules.items():
        oos_pos = np.zeros(n)
        mask = np.zeros(n, dtype=bool)
        thetas = []
        for tr, te in purged_splits(n, HOLD):
            cut = int(len(tr) * 0.8)
            val = tr[cut:]
            theta = pick_theta(sig[val], carry[val])
            thetas.append(theta)
            oos_pos[te] = (sig[te] > theta).astype(float)
            mask[te] = True
        print(f"  {name}: fold thetas {[f'{t*1e4:.1f}bp' for t in thetas]}")
        s = report_carry(f"{name} OOS", oos_pos[mask], carry[mask])
        s["name"], s["oos_pos"], s["mask"] = name, oos_pos, mask
        out.append(s)
    return out


def main() -> None:
    df = build_carry()
    f, prem, carry = df["f"].to_numpy(), df["prem"].to_numpy(), df["carry"].to_numpy()
    n = len(df)
    print(f"carry eval: {n:,} intervals | {df['timestamp'].min():%Y-%m-%d} .. "
          f"{df['timestamp'].max():%Y-%m-%d} | cost {COST_LEG*1e4:.0f} bps/leg-change "
          f"({2*COST_LEG*1e4:.0f} round trip)\n")

    print("— baselines (full sample) —")
    always = report_carry("always-on harvest", np.ones(n), carry)
    rng = np.random.default_rng(0)
    report_carry("random 50% (turnover bite)", rng.choice([0.0, 1.0], n), carry)
    report_carry("funding-sign (f>0)", (f > 0).astype(float), carry)
    report_carry("basis-sign (prem>0)", (prem > 0).astype(float), carry)
    report_carry("both-sides sign(f) [NOT implementable: borrow ignored]",
                 np.sign(f), carry)

    print("\n— theta-rules, OOS (purged walk-forward) —")
    rules = run_rules(f, carry)
    best = max(rules, key=lambda s: s["net_bps_cal"])
    mask = best["mask"]
    always_oos = report_carry("always-on (same OOS rows)", np.ones(int(mask.sum())),
                              carry[mask])

    print("\n— sensitivity: LOW_COST_LEG = 5 bps (VIP/maker; annotation only) —")
    report_carry("always-on harvest", np.ones(n), carry, LOW_COST_LEG)
    report_carry(f"best rule ({best['name']})", best["oos_pos"][mask], carry[mask],
                 LOW_COST_LEG)

    print("\n== VERDICT " + "=" * 55)
    if always["net"] <= 0:
        print("NO HARVESTABLE CARRY: always-on nets <= 0 after costs.")
        print("Stop rule 1 CLOSES the chapter — no further tuning.")
        return
    print(f"stage 1: always-on nets {always['net_bps_cal']:+.2f} bps/8h "
          f"({always['ann_pct']:+.1f} %/yr) after costs -> carry EXISTS at retail fees")
    eligible = best["held"] >= MIN_HELD
    if not eligible or best["net_bps_cal"] <= always_oos["net_bps_cal"]:
        print(f"stage 2: best rule ({best['name']}) nets {best['net_bps_cal']:+.2f} "
              f"bps/8h vs always-on {always_oos['net_bps_cal']:+.2f} on the same "
              f"OOS rows -> NO TIMING EDGE beyond always-on.")
        print("Stop rule 2 CLOSES the chapter: always-on harvest is the honest result")
        print("(signal-quality proxy, not a backtest: margin/liquidation/drawdown ignored).")
    else:
        print(f"stage 2: {best['name']} nets {best['net_bps_cal']:+.2f} bps/8h vs "
              f"always-on {always_oos['net_bps_cal']:+.2f} on the same OOS rows.")
        print("A rule beats always-on OOS — the ML gate MAY open next: deeper")
        print("validation first; never live money from this alone.")


if __name__ == "__main__":
    main()
