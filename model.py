"""ML v2 models, judged STRICTLY out-of-sample vs baselines, in money.

Two models per horizon:
  - logreg: the v1 reference (linear, deliberately dumb);
  - hgb: HistGradientBoostingClassifier — sklearn's gradient-boosted trees
    (LightGBM-style). Catches non-linear interactions logreg can't. No new
    dependency. early_stopping is OFF on purpose: its internal validation
    split is RANDOM, i.e. it shuffles time inside the train fold; we keep
    control of time ourselves.

Abstain threshold tau: the model only bets a direction when its predicted
probability clears tau. tau is picked INSIDE each train fold (fit on the first
80%, choose tau on the last 20%, refit on the whole fold) — picking it on the
test set would make tau one more overfitting knob.

Validation: purged walk-forward (see evaluate.purged_splits) — train only on
the past, cut the H rows whose labels overlap the test window.

STOP RULE (agreed in advance, so the tuning cannot drag on forever):
best config OOS net <= 0 bps/bet after cost => "no edge on v2", iteration
CLOSED. A positive number would mean deeper validation next — never live
money from this alone.
"""
from __future__ import annotations

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from dataset import FEATURES, build_dataset
from evaluate import COST, HORIZONS, purged_splits, report

# candidate abstain thresholds; 0.0 = "always bet whatever argmax says"
# (3 classes -> max proba is always >= ~0.34, so 0.40+ actually filters)
TAUS = (0.0, 0.40, 0.45, 0.50, 0.55, 0.60)
MIN_VAL_BETS = 30       # a tau that leaves fewer val bets than this is noise

MODELS = {
    "logreg": lambda: make_pipeline(
        StandardScaler(),
        LogisticRegression(max_iter=1000, class_weight="balanced"),
    ),
    "hgb": lambda: HistGradientBoostingClassifier(
        max_iter=200,
        learning_rate=0.1,
        min_samples_leaf=100,       # ~100k rows: coarse leaves, less memorizing
        l2_regularization=1.0,
        early_stopping=False,
        class_weight="balanced",    # same reason as logreg: FLAT dominates
        random_state=0,
    ),
}


def bets_from_proba(clf, X: np.ndarray, tau: float) -> np.ndarray:
    """argmax class, but abstain (0) when a directional call is below tau."""
    proba = clf.predict_proba(X)
    pred = clf.classes_[proba.argmax(axis=1)].astype(int)
    conf = proba.max(axis=1)
    pred[(pred != 0) & (conf < tau)] = 0
    return pred


def pick_tau(clf, X_val: np.ndarray, fwd_val: np.ndarray) -> float:
    """tau maximizing net bps/bet on the validation slice (train data only)."""
    best_tau, best_net = 0.0, -np.inf
    for tau in TAUS:
        pos = bets_from_proba(clf, X_val, tau)
        bet = pos != 0
        if bet.sum() < MIN_VAL_BETS:
            continue
        net = (pos[bet] * fwd_val[bet] - COST).mean()
        if net > best_net:
            best_net, best_tau = net, tau
    return best_tau


def run_horizon(H: int) -> list[dict]:
    df = build_dataset(horizon=H, cost=COST)
    X = df[FEATURES].to_numpy()
    y = df["label"].to_numpy()
    fwd = df["fwd_ret"].to_numpy()
    n = len(df)

    print(f"\n=== H={H}m | {n:,} bars | flat {np.mean(y == 0):.1%} ===")
    out, mask = [], None
    for name, make in MODELS.items():
        oos = np.full(n, np.nan)
        taus, fold_nets = [], []
        for tr, te in purged_splits(n, H):
            # inner time-ordered split for tau; purge H rows there too
            cut = int(len(tr) * 0.8)
            fit_i, val_i = tr[:max(cut - H, 1)], tr[cut:]
            inner = make().fit(X[fit_i], y[fit_i])
            tau = pick_tau(inner, X[val_i], fwd[val_i])

            clf = make().fit(X[tr], y[tr])          # refit on the full fold
            pos = bets_from_proba(clf, X[te], tau)
            oos[te] = pos
            taus.append(tau)
            b = pos != 0
            fold_nets.append(
                float((pos[b] * fwd[te][b] - COST).mean() * 1e4) if b.any() else np.nan
            )

        mask = ~np.isnan(oos)                        # identical for both models
        pos = oos[mask].astype(int)
        nets_txt = ", ".join("—" if x != x else f"{x:+.1f}" for x in fold_nets)
        print(f"{name}: fold nets [{nets_txt}] bps | tau per fold {taus}")
        report(f"model {name}", pos, fwd[mask])

        b = pos != 0
        out.append(dict(
            H=H, model=name,
            bets=int(b.sum()),
            net=float((pos[b] * fwd[mask][b] - COST).mean() * 1e4) if b.any() else np.nan,
            pos_folds=sum(1 for x in fold_nets if x == x and x > 0),
            n_folds=len(fold_nets),
        ))

    # baselines on the SAME out-of-sample rows — apples to apples
    report("baseline imb-sign (15m)", np.sign(df["imb_15"].to_numpy()[mask]), fwd[mask])
    report("baseline always-long", np.ones(int(mask.sum())), fwd[mask])
    return out


def main() -> None:
    rows: list[dict] = []
    for H in HORIZONS:
        rows += run_horizon(H)

    print("\n========== VERDICT (stop rule agreed in advance) ==========")
    for r in rows:
        net = "   n/a" if r["net"] != r["net"] else f"{r['net']:+6.1f}"
        print(f"H={r['H']:3d}m {r['model']:7s} | bets {r['bets']:6d} "
              f"| net {net} bps/bet | folds>0: {r['pos_folds']}/{r['n_folds']}")

    valid = [r for r in rows if r["bets"] >= 100 and r["net"] == r["net"]]
    best = max(valid, key=lambda r: r["net"]) if valid else None
    if best is None or best["net"] <= 0:
        print("\nNO EDGE on v2: best config nets <= 0 bps/bet after cost")
        print("(or never places enough bets to judge). Per the stop rule this")
        print("CLOSES the iteration — no further tuning.")
    else:
        print(f"\nbest: H={best['H']}m {best['model']} at {best['net']:+.1f} bps/bet "
              f"({best['bets']} bets, folds>0 {best['pos_folds']}/{best['n_folds']}).")
        print("A positive number here is NOT yet an edge — next would be deeper")
        print("validation (regime split, longer history). Never live money from this alone.")


if __name__ == "__main__":
    main()
