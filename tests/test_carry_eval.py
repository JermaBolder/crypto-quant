"""Episode-costed carry PnL: costs charged on TURNOVER (entry + changes +
final close), never per held interval — the structural difference from
evaluate.py's per-bet costing, so the math gets pinned exactly.
"""
from __future__ import annotations

import numpy as np
import pytest

from carry_eval import COST_LEG, episode_stats, pick_theta, turnover


def test_turnover_entry_changes_and_final_close():
    assert turnover(np.array([0.0, 0.0, 0.0])) == 0.0
    assert turnover(np.array([1.0, 1.0, 1.0])) == 2.0        # enter once, close once
    assert turnover(np.array([1.0, 0.0, 1.0])) == 4.0        # two round trips
    assert turnover(np.array([-1.0, 1.0])) == 4.0            # entry 1 + flip 2 + close 1


def test_always_on_with_zero_carry_costs_one_round_trip():
    s = episode_stats(np.ones(90), np.zeros(90))
    assert s["net"] == pytest.approx(-2 * COST_LEG)          # entry + final close, ONCE
    assert s["episodes"] == 1
    assert s["held"] == 90


def test_two_episodes_cost_four_leg_changes():
    pos = np.array([1.0, 1.0, 0.0, 0.0, 1.0, 1.0])
    s = episode_stats(pos, np.zeros(6))
    assert s["episodes"] == 2
    assert s["net"] == pytest.approx(-4 * COST_LEG)


def test_exact_net_on_hand_numbers():
    pos = np.array([1.0, 0.0, 1.0])
    carry = np.array([0.001, 0.002, 0.003])
    s = episode_stats(pos, carry)
    assert s["gross"] == pytest.approx(0.004)                # 0.001 + 0.003
    assert s["net"] == pytest.approx(0.004 - 4 * COST_LEG)
    assert s["held"] == 2 and s["episodes"] == 2
    assert s["hit"] == 1.0
    assert s["net_bps_cal"] == pytest.approx(s["net"] / 3 * 1e4)


def test_pick_theta_filters_the_losing_weak_funding_intervals():
    # repeating [good, weak, negative]: theta=1bp keeps only the good ones
    # (weak sits strictly between the 0.5bp and 1bp thresholds)
    f = np.tile([4e-4, 0.8e-4, -1e-4], 50)
    carry = np.where(f > 1e-4, 6e-4, -6e-4)
    assert pick_theta(f, carry) == pytest.approx(1e-4)


def test_pick_theta_skips_starving_thetas_even_if_profitable():
    # 10 spectacular intervals above 5e-5, but 10 < MIN_HELD//3 -> skipped;
    # theta=0 holds 50 mediocre ones and is the only eligible choice
    f = np.concatenate([np.full(40, 1e-5), np.full(10, 1e-3), np.full(70, -1e-4)])
    carry = np.where(f >= 1e-3, 1e-2, 1e-5)
    assert pick_theta(f, carry) == 0.0
