"""Carry chapter dataset: 8h funding intervals with basis mark-to-market.

The carry position is SHORT perp + LONG spot (delta-neutral): it receives the
funding payment when the rate is positive and pays when negative, while the
basis (premium index) marks against the short perp leg.

Timing convention (leakage is the cardinal sin, same as dataset.py):
  Row i sits at snapped funding time t_i (00/08/16 UTC grid).
    f     = rate SETTLED at t_i        - known at t_i, signal side
    prem  = premium index OPEN at t_i  - the value at that instant, signal side
  Holding the position through (t_i, t_i+8h] realizes:
    carry = f.shift(-1) - (prem.shift(-1) - prem)
  i.e. the funding settled at the NEXT boundary (it accrues over the interval
  we hold - it is NOT known at t_i) plus the basis P&L of the short perp leg.
  f.shift(-1) and prem.shift(-1) are the ONLY look-forwards.

The exchange stamps funding with millisecond jitter (...00005), so timestamps
are snapped to the 8h grid with a fail-loud tolerance: anything further than
TOL from its slot, or two events landing in one slot, raises instead of
silently shifting data. Intervals whose next event is not exactly 8h away
(exchange incidents) are dropped and COUNTED - the stats print them.
"""
from __future__ import annotations

import io
import urllib.parse
import urllib.request

import numpy as np
import pandas as pd

from config import QUESTDB_HTTP

QUESTDB_EXP = f"{QUESTDB_HTTP}/exp"
GRID = pd.Timedelta(hours=8)
TOL = pd.Timedelta(minutes=5)


def _export(sql: str) -> pd.DataFrame:
    url = QUESTDB_EXP + "?" + urllib.parse.urlencode({"query": sql})
    raw = urllib.request.urlopen(url, timeout=120).read().decode()
    df = pd.read_csv(io.StringIO(raw), parse_dates=["timestamp"])
    return df.sort_values("timestamp").reset_index(drop=True)


def load_funding() -> pd.DataFrame:
    return _export("SELECT timestamp, rate, interval_hours FROM funding")


def load_premium() -> pd.DataFrame:
    return _export("SELECT timestamp, o FROM premium_index_1h")


def snap_to_grid(ts: pd.Series, grid: pd.Timedelta = GRID, tol: pd.Timedelta = TOL) -> pd.Series:
    """Round to the 8h grid (epoch-aligned = 00/08/16 UTC). Fail loud on
    anything that is not just jitter: big offsets or two events in one slot."""
    snapped = ts.dt.round(grid)
    off = (ts - snapped).abs()
    if (off > tol).any():
        bad = ts[off > tol].iloc[0]
        raise ValueError(f"funding timestamp {bad} is > {tol} from the {grid} grid")
    if snapped.duplicated().any():
        raise ValueError("two funding events snapped into one grid slot")
    return snapped


def build_carry() -> pd.DataFrame:
    """One row per 8h funding interval: signal columns (f, prem) + realized carry."""
    f = load_funding()
    assert (f["interval_hours"] == 8).all(), "8h funding assumption violated"
    df = pd.DataFrame({"timestamp": snap_to_grid(f["timestamp"]), "f": f["rate"]})

    prem = load_premium().rename(columns={"o": "prem"})
    df = df.merge(prem, on="timestamp", how="left")   # premium OPEN at the boundary

    gap_ok = df["timestamp"].shift(-1) - df["timestamp"] == GRID
    df["carry"] = df["f"].shift(-1) - (df["prem"].shift(-1) - df["prem"])
    df.loc[~gap_ok, "carry"] = np.nan                 # broken interval: no honest payoff

    n_raw = len(df)
    df = df.dropna(subset=["f", "prem", "carry"]).reset_index(drop=True)
    df.attrs["dropped"] = n_raw - len(df)
    return df


if __name__ == "__main__":
    df = build_carry()
    bps = 1e4
    f, prem, carry = df["f"], df["prem"], df["carry"]
    print(f"rows {len(df):,} | {df['timestamp'].min():%Y-%m-%d} .. "
          f"{df['timestamp'].max():%Y-%m-%d} | dropped {df.attrs['dropped']} "
          f"(gaps/missing premium/last row)")
    print(f"funding bps/8h: mean {f.mean()*bps:+.2f}  median {f.median()*bps:+.2f}  "
          f"p5 {f.quantile(0.05)*bps:+.2f}  p95 {f.quantile(0.95)*bps:+.2f}  "
          f"positive {(f > 0).mean():.1%}")
    print(f"funding autocorr: lag1 {f.autocorr(1):+.2f}  lag3 {f.autocorr(3):+.2f}  "
          f"lag9 {f.autocorr(9):+.2f}  lag30 {f.autocorr(30):+.2f}")
    print(f"premium bps: mean {prem.mean()*bps:+.2f}  std {prem.std()*bps:.2f}")
    # sum(d_prem) telescopes to prem_end - prem_start ~ 0, so always-on gross
    # ~ mean funding; print both so the reader sees the decomposition
    ipy = 3 * 365
    print(f"always-on gross: {carry.mean()*bps:+.2f} bps/8h "
          f"(funding part {f.shift(-1).dropna().mean()*bps:+.2f}) "
          f"= {carry.mean()*ipy*100:+.1f} %/yr | one round-trip cost = 28 bps")
