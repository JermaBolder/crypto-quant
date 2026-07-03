"""build_dataset on synthetic bars (load_bars monkeypatched — no QuestDB).

The property under test is the vol-scaled dead-zone label: quiet regimes stay
FLAT, real moves get a sign, and the threshold scales with local volatility.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import dataset
from dataset import FEATURES, build_dataset

HORIZON = 15
COST = 0.0015


def make_bars(n: int = 240, price0: float = 50_000.0, wiggle_bp: float = 1.0) -> pd.DataFrame:
    """Synthetic 1m bars in the exact 13-column shape load_bars returns.

    Price alternates +/- wiggle_bp around price0: rolling vol is positive (a
    constant price would NaN vol_ratio and dropna would eat every row) but the
    moves are far below the cost floor, so the quiet market is genuinely quiet.
    """
    c = price0 * (1.0 + (np.arange(n) % 2) * wiggle_bp / 10_000.0)
    o = np.roll(c, 1)
    o[0] = price0
    h = np.maximum(o, c) * (1 + 2e-5)
    l = np.minimum(o, c) * (1 - 2e-5)  # noqa: E741
    vol = np.full(n, 10.0)
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-05", periods=n, freq="min", tz="UTC"),
        "o": o, "h": h, "l": l, "c": c,
        "vol": vol,
        "pv": c * vol,
        "delta": np.full(n, 1.0),
        "avg_size": np.full(n, 0.02),
        "max_size": np.full(n, 0.5),
        "vol_big": np.full(n, 2.0),
        "delta_big": np.full(n, 0.5),
        "n": np.full(n, 500),
    })


def with_step(bars: pd.DataFrame, at: int, pct: float) -> pd.DataFrame:
    """Permanent price step of `pct` starting at bar `at` (o/h/l/c/pv together)."""
    bars = bars.copy()
    for col in ("o", "h", "l", "c"):
        bars.loc[at:, col] *= 1.0 + pct
    bars["pv"] = bars["c"] * bars["vol"]
    return bars


def build(monkeypatch, bars: pd.DataFrame, **kw) -> pd.DataFrame:
    monkeypatch.setattr(dataset, "load_bars", lambda: bars.copy())
    return build_dataset(horizon=HORIZON, cost=COST, **kw)


def test_features_list_is_22_and_all_computed(monkeypatch):
    assert len(FEATURES) == 22
    df = build(monkeypatch, make_bars())
    assert len(df) > 100
    assert not df[FEATURES + ["fwd_ret", "thr", "label"]].isna().any().any()


def test_quiet_market_is_all_flat_and_thr_keeps_cost_floor(monkeypatch):
    df = build(monkeypatch, make_bars())
    assert (df["label"] == 0).all()          # 1bp wiggles never clear a 15bp cost floor
    assert (df["thr"] >= COST).all()         # dead zone never shrinks below cost


def test_step_up_labels_long_before_the_jump(monkeypatch):
    jump = 150
    bars = with_step(make_bars(), at=jump, pct=0.02)
    df = build(monkeypatch, bars)
    t0 = bars["timestamp"].iloc[0]
    # rows whose 15-bar forward window crosses the +2% step
    pre = df[(df["timestamp"] >= t0 + pd.Timedelta(minutes=jump - HORIZON))
             & (df["timestamp"] < t0 + pd.Timedelta(minutes=jump))]
    assert len(pre) == HORIZON
    assert (pre["label"] == 1).all()


def test_step_down_labels_short_before_the_drop(monkeypatch):
    jump = 150
    bars = with_step(make_bars(), at=jump, pct=-0.02)
    df = build(monkeypatch, bars)
    t0 = bars["timestamp"].iloc[0]
    pre = df[(df["timestamp"] >= t0 + pd.Timedelta(minutes=jump - HORIZON))
             & (df["timestamp"] < t0 + pd.Timedelta(minutes=jump))]
    assert (pre["label"] == -1).all()


def test_label_matches_threshold_invariant(monkeypatch):
    df = build(monkeypatch, with_step(make_bars(), at=150, pct=0.02))
    assert ((df["fwd_ret"] > df["thr"]) == (df["label"] == 1)).all()
    assert ((df["fwd_ret"] < -df["thr"]) == (df["label"] == -1)).all()


def test_dead_zone_scales_with_local_vol(monkeypatch):
    quiet = build(monkeypatch, make_bars(wiggle_bp=1.0))
    wild = build(monkeypatch, make_bars(wiggle_bp=50.0))
    assert wild["thr"].median() > quiet["thr"].median()
    # and k=0 collapses the dead zone to the pure cost floor
    flat_k = build(monkeypatch, make_bars(), k=0.0)
    assert (flat_k["thr"] == COST).all()


def test_last_horizon_rows_have_no_future_and_are_dropped(monkeypatch):
    bars = make_bars()
    df = build(monkeypatch, bars)
    assert df["timestamp"].max() <= bars["timestamp"].max() - pd.Timedelta(minutes=HORIZON)
