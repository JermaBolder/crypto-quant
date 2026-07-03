"""ML v2: the honesty harness — baselines BEFORE any model, plus the
purged walk-forward split that both this file and model.py use.

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

from collections.abc import Iterator

import numpy as np
from sklearn.model_selection import TimeSeriesSplit

from dataset import build_dataset

COST = 0.0015    # round-trip cost (~15 bps), charged once per directional bet
HORIZONS = (5, 15, 60)


def purged_splits(n_rows: int, horizon: int,
                  n_splits: int = 5) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """TimeSeriesSplit with the last `horizon` rows CUT OFF the train fold.

    WHY: row t's label is computed from closes up to t+H. Without purging, the
    labels of the last H train rows are computed from the first test rows —
    the model trains on a piece of the future it is then scored on. Small
    leak, systematically optimistic, classic. Purging the boundary kills it.
    """
    for tr, te in TimeSeriesSplit(n_splits=n_splits).split(np.arange(n_rows)):
        yield tr[tr < te.min() - horizon], te


def report(name: str, pos: np.ndarray, fwd: np.ndarray) -> None:
    bet = pos != 0
    nb = int(bet.sum())
    if nb == 0:
        print(f"{name:26s} | no bets")
        return
    hit = (np.sign(fwd[bet]) == pos[bet]).mean()
    pnl = pos[bet] * fwd[bet] - COST            # net of cost, per bet, in fraction
    print(f"{name:26s} | bets {nb:6d} ({nb/len(pos):3.0%}) | hit {hit:5.1%} "
          f"| net {pnl.mean()*1e4:+6.1f} bps/bet | total {pnl.sum()*1e4:+8.0f} bps")


def main() -> None:
    for H in HORIZONS:
        df = build_dataset(horizon=H, cost=COST)
        fwd = df["fwd_ret"].to_numpy()
        label = df["label"].to_numpy()
        n = len(df)
        rng = np.random.default_rng(0)

        print(f"\n=== H={H}m | {n:,} bars | flat {np.mean(label == 0):.1%} "
              f"<- always-FLAT accuracy bar ===")
        report("always long", np.ones(n), fwd)
        report("random direction", rng.choice([-1, 1], n), fwd)
        report("imbalance-sign (15m)", np.sign(df["imb_15"].to_numpy()), fwd)
        report("momentum-sign (5m)", np.sign(df["mom_5"].to_numpy()), fwd)

    print("\n^ if these sit at/below 0 bps/bet after cost, there is no free edge —")
    print("  the honest expectation, and the bar any model must clear.")


if __name__ == "__main__":
    main()
