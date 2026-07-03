"""purged_splits: the purge gap is the whole point — labels look H bars ahead,
so the last H train rows would be computed from test-window prices (leakage).
"""
from __future__ import annotations

from evaluate import purged_splits

HORIZON = 15


def test_five_folds_with_purge_gap():
    folds = list(purged_splits(1000, horizon=HORIZON))
    assert len(folds) == 5
    for tr, te in folds:
        assert tr.max() < te.min() - HORIZON     # the gap: no train label sees test prices
        assert tr.min() == 0                     # expanding window from the start
        assert len(tr) > 0 and len(te) > 0


def test_test_windows_ascend_and_do_not_overlap():
    folds = list(purged_splits(1000, horizon=HORIZON))
    for (_, te_a), (_, te_b) in zip(folds, folds[1:], strict=False):
        assert te_a.max() < te_b.min()
