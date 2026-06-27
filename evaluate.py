"""Phase 2 step 3b: the honesty harness — baselines BEFORE any model.

Before training anything, measure what TRIVIAL strategies score. A model that
can't beat these is worthless. Per strategy we report:
  - hit: of the bars where it bet a direction, how often the sign was right;
  - net PnL (bps/bet): realized forward return in the bet direction, minus
    round-trip cost. THIS is the bottom line — accuracy is a vanity metric.

The toy PnL is a signal-quality proxy, NOT a real backtest: it ignores
overlapping positions, slippage and execution. Read it as "is there ANY edge",
not "this is the return you'd get".
"""
from __future__ import annotations

import numpy as np

from dataset import build_dataset

COST = 0.0015  # round-trip cost (~15 bps), charged once per directional bet


def report(name: str, pos: np.ndarray, fwd: np.ndarray) -> None:
    bet = pos != 0
    nb = int(bet.sum())
    if nb == 0:
        print(f"{name:22s} | no bets")
        return
    hit = (np.sign(fwd[bet]) == pos[bet]).mean()
    pnl = pos[bet] * fwd[bet] - COST            # net of cost, per bet, in fraction
    print(f"{name:22s} | bets {nb:6d} ({nb/len(pos):3.0%}) | hit {hit:5.1%} "
          f"| net {pnl.mean()*1e4:+6.1f} bps/bet | total {pnl.sum()*1e4:+8.0f} bps")


def main() -> None:
    df = build_dataset(horizon=15, cost=COST)
    fwd = df["fwd_ret"].to_numpy()
    label = df["label"].to_numpy()
    n = len(df)
    rng = np.random.default_rng(0)

    print(f"dataset {n:,} bars | flat {np.mean(label == 0):.1%}")
    print(f"always-FLAT accuracy = {np.mean(label == 0):.1%}  <- the accuracy bar\n")

    report("always long", np.ones(n), fwd)
    report("random direction", rng.choice([-1, 1], n), fwd)
    report("delta-sign (roll15)", np.sign(df["roll_delta_15"].to_numpy()), fwd)
    report("momentum-sign (5m)", np.sign(df["mom_5"].to_numpy()), fwd)

    print("\n^ if these sit at/below 0 bps/bet after cost, there's no free edge —")
    print("  the honest expectation, and the bar any model must clear.")


if __name__ == "__main__":
    main()
