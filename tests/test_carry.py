"""build_carry on synthetic funding/premium frames (load_* monkeypatched).

Pins the timing contract: signal columns come from data at t only, the carry
payoff is the ONLY look-forward, jitter snaps to the grid but real offsets
fail loud, and broken intervals are dropped rather than bridged.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import carry
from carry import build_carry, snap_to_grid


def funding_frame(n: int = 12, rate: float = 1e-4, jitter_ms: int = 5) -> pd.DataFrame:
    ts = pd.date_range("2024-01-01", periods=n, freq="8h", tz="UTC")
    ts = ts + pd.Timedelta(milliseconds=jitter_ms)     # exchange-style ms jitter
    return pd.DataFrame({"timestamp": ts, "rate": np.full(n, rate),
                         "interval_hours": np.full(n, 8)})


def premium_frame(n: int = 12, values: list[float] | None = None) -> pd.DataFrame:
    ts = pd.date_range("2024-01-01", periods=n, freq="8h", tz="UTC")
    vals = values if values is not None else [1e-4] * n
    return pd.DataFrame({"timestamp": ts, "o": vals})


def build(monkeypatch, f: pd.DataFrame, p: pd.DataFrame) -> pd.DataFrame:
    monkeypatch.setattr(carry, "load_funding", lambda: f)
    monkeypatch.setattr(carry, "load_premium", lambda: p)
    return build_carry()


def test_snap_kills_ms_jitter():
    ts = pd.Series(pd.to_datetime(["2024-01-01T00:00:00.005Z", "2024-01-01T08:00:00.002Z"]))
    snapped = snap_to_grid(ts)
    assert snapped.iloc[0] == pd.Timestamp("2024-01-01T00:00:00Z")
    assert snapped.iloc[1] == pd.Timestamp("2024-01-01T08:00:00Z")


def test_snap_fails_loud_beyond_tolerance():
    ts = pd.Series(pd.to_datetime(["2024-01-01T00:07:00Z"]))     # 7 min > 5 min TOL
    with pytest.raises(ValueError, match="grid"):
        snap_to_grid(ts)


def test_snap_fails_loud_on_slot_collision():
    ts = pd.Series(pd.to_datetime(["2024-01-01T00:00:00.001Z", "2024-01-01T00:00:00.002Z"]))
    with pytest.raises(ValueError, match="one grid slot"):
        snap_to_grid(ts)


def test_carry_is_next_funding_minus_premium_drift(monkeypatch):
    # f constant 1bp; premium goes 1bp -> 3bp over the first interval
    prem = [1e-4, 3e-4] + [3e-4] * 10
    df = build(monkeypatch, funding_frame(), premium_frame(values=prem))
    # carry_0 = f_1 - (prem_1 - prem_0) = 1e-4 - 2e-4 = -1e-4
    assert df["carry"].iloc[0] == pytest.approx(-1e-4)
    # flat premium afterwards: carry = funding alone
    assert df["carry"].iloc[1] == pytest.approx(1e-4)


def test_rising_premium_hurts_the_short(monkeypatch):
    rising = premium_frame(values=list(np.linspace(0, 1e-2, 12)))   # basis grinds up
    df = build(monkeypatch, funding_frame(rate=0.0), rising)
    assert (df["carry"] < 0).all()                                  # short perp bleeds


def test_gap_and_last_rows_are_dropped_and_counted(monkeypatch):
    f = funding_frame()
    f = f.drop(index=5).reset_index(drop=True)        # one missing funding event
    df = build(monkeypatch, f, premium_frame())
    # 11 funding events survive; the one before the hole (16h to next) and the
    # last one (no forward) have no honest payoff -> dropped and counted
    assert len(df) == 9
    assert df.attrs["dropped"] == 2
    # the row BEFORE the hole (its 8h payoff window is broken) must be gone
    hole_neighbor = pd.Timestamp("2024-01-02T08:00:00Z")   # slot 4
    assert hole_neighbor not in set(df["timestamp"])
    assert df["timestamp"].iloc[-1] == pd.Timestamp("2024-01-04T08:00:00Z")  # slot 10, not 11


def test_signal_columns_immune_to_future_premium(monkeypatch):
    base = build(monkeypatch, funding_frame(), premium_frame())
    bumped_prem = premium_frame(values=[1e-4] * 11 + [5e-3])        # change only the future
    bumped = build(monkeypatch, funding_frame(), bumped_prem)
    # signal side identical; only the last realized carry moved
    pd.testing.assert_series_equal(base["f"], bumped["f"])
    pd.testing.assert_series_equal(base["prem"], bumped["prem"])
    assert base["carry"].iloc[-1] != bumped["carry"].iloc[-1]
