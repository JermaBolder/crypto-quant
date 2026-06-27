"""Phase 2 step 3c: minimal model, judged STRICTLY walk-forward vs baselines.

Model: StandardScaler + LogisticRegression (multinomial), deliberately dumb.
Validation: TimeSeriesSplit — train only on the past, test on the future, roll
forward. The scaler is fit on the TRAIN fold only (fitting on all data would
leak test-set statistics into training).

class_weight='balanced' on purpose: 66% of labels are FLAT, so an unweighted
model just predicts FLAT forever and never bets — nothing to judge. Balancing
forces it to actually call directions, so we can test whether those calls pay.

We judge it the same honest way as the baselines (hit-rate + net PnL after cost
on the bars where it bets). If it can't beat 'always long' / 'delta-sign'
out-of-sample, it has no edge — the likely outcome, and that's a real result.
"""
from __future__ import annotations

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from dataset import FEATURES, build_dataset
from evaluate import COST, report


def main() -> None:
    df = build_dataset(horizon=15, cost=COST)
    X = df[FEATURES].to_numpy()
    y = df["label"].to_numpy()
    fwd = df["fwd_ret"].to_numpy()
    n = len(df)

    oos_pred = np.full(n, np.nan)   # model prediction per row, out-of-sample only
    for k, (tr, te) in enumerate(TimeSeriesSplit(n_splits=5).split(X), 1):
        clf = make_pipeline(
            StandardScaler(),
            LogisticRegression(max_iter=1000, class_weight="balanced"),
        )
        clf.fit(X[tr], y[tr])
        oos_pred[te] = clf.predict(X[te])
        print(f"fold {k}: train {len(tr):5d} -> test {len(te):5d} | "
              f"acc {(oos_pred[te] == y[te]).mean():.1%}")

    m = ~np.isnan(oos_pred)   # the first block is never tested -> excluded
    print(f"\nout-of-sample rows: {int(m.sum()):,}")
    print(f"always-FLAT accuracy (OOS) = {np.mean(y[m] == 0):.1%}")
    print(f"model accuracy      (OOS) = {np.mean(oos_pred[m] == y[m]):.1%}\n")

    # apples-to-apples on the SAME out-of-sample rows
    report("model (logreg)", oos_pred[m].astype(int), fwd[m])
    report("delta-sign", np.sign(df["roll_delta_15"].to_numpy()[m]), fwd[m])
    report("always long", np.ones(int(m.sum())), fwd[m])

    print("\nverdict: the model has an edge ONLY if its net bps/bet is clearly")
    print("positive AND above the baselines. Anything else = no edge.")


if __name__ == "__main__":
    main()
