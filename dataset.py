"""Phase 2 step 3a: build the (features, label) dataset from order-flow bars.

Pulls 1-minute order-flow bars from QuestDB (agg_trades, SAMPLE BY 1m), then:
  - features: computed ONLY from the current and PAST bars (no look-ahead);
  - label: sign of the forward H-bar return, with a 'cost' dead-zone, so we
    don't even try to predict moves smaller than what trading them would cost.

Leakage is the cardinal sin, so every feature uses pct_change/rolling over PAST
bars; only the label looks forward (shift(-H)) — that is the thing we predict.

Timing convention (anti-leakage): bar t closes at t+1, so bar t's numbers are
actionable at t+1. We therefore measure the forward return from close[t] to
close[t+H], and all features use bars <= t.
"""
from __future__ import annotations

import io
import sys
import urllib.parse
import urllib.request

import numpy as np
import pandas as pd

QUESTDB_EXP = "http://127.0.0.1:9000/exp"

BARS_SQL = """
SELECT timestamp,
       first(price) o, max(price) h, min(price) l, last(price) c,
       sum(size) vol,
       sum(size * CASE WHEN side='BUY' THEN 1 ELSE -1 END) delta,
       count() n
FROM agg_trades
SAMPLE BY 1m
"""

FEATURES = ["ret1", "delta_norm", "roll_delta_5", "roll_delta_15",
            "roll_vol_5", "mom_5", "mom_15", "volat_15", "n"]


def load_bars() -> pd.DataFrame:
    """1-minute order-flow bars from QuestDB as a DataFrame (CSV via /exp)."""
    url = QUESTDB_EXP + "?" + urllib.parse.urlencode({"query": BARS_SQL})
    raw = urllib.request.urlopen(url, timeout=60).read().decode()
    df = pd.read_csv(io.StringIO(raw), parse_dates=["timestamp"])
    return df.sort_values("timestamp").reset_index(drop=True)


def build_dataset(horizon: int = 15, cost: float = 0.0015) -> pd.DataFrame:
    """bars + features + label. horizon in bars(min); cost = dead-zone fraction."""
    df = load_bars()

    # --- features: PAST/PRESENT only (no future) ---
    df["ret1"] = df["c"].pct_change(fill_method=None)
    df["delta_norm"] = df["delta"] / df["vol"].replace(0, np.nan)  # normalized aggression
    df["roll_delta_5"] = df["delta"].rolling(5).sum()
    df["roll_delta_15"] = df["delta"].rolling(15).sum()
    df["roll_vol_5"] = df["vol"].rolling(5).sum()
    df["mom_5"] = df["c"].pct_change(5, fill_method=None)
    df["mom_15"] = df["c"].pct_change(15, fill_method=None)
    df["volat_15"] = df["ret1"].rolling(15).std()

    # --- label: forward return over `horizon`, then dead-zoned sign ---
    df["fwd_ret"] = df["c"].shift(-horizon) / df["c"] - 1.0   # the ONLY look-forward
    df["label"] = 0
    df.loc[df["fwd_ret"] > cost, "label"] = 1
    df.loc[df["fwd_ret"] < -cost, "label"] = -1

    # drop warm-up rows (rolling NaNs) and the last `horizon` rows (no future)
    df = df.dropna(subset=FEATURES + ["fwd_ret"]).reset_index(drop=True)
    return df


if __name__ == "__main__":
    H = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    cost = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0015
    d = build_dataset(horizon=H, cost=cost)
    print(f"dataset: {len(d):,} rows | {len(FEATURES)} features | horizon={H}m | cost={cost}")
    print(f"range: {d['timestamp'].min()} .. {d['timestamp'].max()}")
    print("\nlabel counts (-1 down / 0 flat / +1 up):")
    print(d["label"].value_counts().sort_index().to_string())
    print(f"\nflat fraction: {(d['label'] == 0).mean():.1%}")
    print("\ntail:")
    print(d[["timestamp", "c", "delta", "fwd_ret", "label"]].tail(3).to_string(index=False))
