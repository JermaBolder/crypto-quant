"""bets_from_proba / pick_tau with a stub classifier — no fitting, no data.

The abstain logic is what turns probabilities into money decisions, so its
edges (FLAT immunity, the strict < at tau, the MIN_VAL_BETS guard) get pinned.
"""
from __future__ import annotations

import numpy as np

from model import MIN_VAL_BETS, bets_from_proba, pick_tau


class StubClf:
    """predict_proba returns canned rows; column order matches classes_."""

    classes_ = np.array([-1, 0, 1])

    def __init__(self, proba: list[list[float]]) -> None:
        self.proba = np.asarray(proba, dtype=float)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        assert len(X) == len(self.proba)
        return self.proba


def X(n: int) -> np.ndarray:
    return np.zeros((n, 1))


def test_tau_zero_is_pure_argmax():
    clf = StubClf([[0.7, 0.2, 0.1], [0.1, 0.8, 0.1], [0.2, 0.2, 0.6]])
    assert bets_from_proba(clf, X(3), tau=0.0).tolist() == [-1, 0, 1]


def test_directional_call_below_tau_abstains_but_at_tau_bets():
    clf = StubClf([[0.5, 0.1, 0.4], [0.6, 0.2, 0.2]])
    out = bets_from_proba(clf, X(2), tau=0.6)
    assert out.tolist() == [0, -1]               # conf<tau -> abstain; conf==tau -> bet (strict <)


def test_flat_argmax_stays_flat_at_any_tau():
    clf = StubClf([[0.1, 0.8, 0.1]])
    assert bets_from_proba(clf, X(1), tau=0.99).tolist() == [0]


def test_pick_tau_filters_out_the_losing_low_confidence_bets():
    # 30 confident longs that win big, 30 shaky shorts that lose:
    # tau=0.50 is the first threshold that drops the shaky ones (conf 0.46)
    proba = [[0.05, 0.05, 0.90]] * 30 + [[0.46, 0.09, 0.45]] * 30
    fwd = np.array([0.01] * 60)                  # shorts bet against a +1% move
    assert pick_tau(StubClf(proba), X(60), fwd) == 0.50


def test_pick_tau_skips_taus_below_min_val_bets():
    # only 20 confident rows: tau=0.50 would be the most profitable but leaves
    # 20 < MIN_VAL_BETS bets, so it must be skipped and tau stays at 0.0
    assert MIN_VAL_BETS == 30
    proba = [[0.05, 0.05, 0.90]] * 20 + [[0.46, 0.09, 0.45]] * 40
    fwd = np.array([0.01] * 20 + [0.001] * 40)   # low-conf shorts bleed slowly
    assert pick_tau(StubClf(proba), X(60), fwd) == 0.0
