"""ML v2: build the (features, label) dataset from order-flow bars.

Pulls 1-minute order-flow bars from QuestDB (agg_trades, SAMPLE BY 1m), then:
  - features: computed ONLY from current and PAST bars (no look-ahead);
  - label: sign of the forward H-bar return with a VOLATILITY-SCALED dead zone.

What changed vs v1 (and WHY):
  - label dead zone = cost + k * sigma_H, where sigma_H is the local 1m vol
    scaled to the horizon. A fixed 15 bps zone marks everything FLAT in quiet
    regimes and everything "signal" in wild ones; scaling by local vol keeps
    the class mix comparable across regimes. The cost floor stays — a move
    smaller than round-trip cost is never worth predicting.
  - features are RATIOS, not raw sums. Raw delta/count/volume drift with
    overall market activity across 90 days; a linear model then learns the
    calendar, not the market. Imbalance ratios and activity-vs-own-1h-average
    are regime-stable.
  - new families: trade-size structure (what the big players do), rolling-VWAP
    distance, bar geometry, vol-regime ratio, time-of-day (crypto has real
    intraday seasonality; hour as sin/cos so 23:00 and 00:00 are neighbors).

Leakage is the cardinal sin: every feature uses only bars <= t; only the label
looks forward (shift(-H)) — that is the thing we predict.
"""
from __future__ import annotations

import io
import sys
import urllib.parse
import urllib.request

import numpy as np
import pandas as pd

from config import QUESTDB_HTTP

QUESTDB_EXP = f"{QUESTDB_HTTP}/exp"

# "Big" trade threshold in BTC. Grounded on the data, not invented: avg trade
# is 0.019 BTC, p99 is 0.36 -> 0.35 = "top ~1% of prints". One fixed scalar
# (an operational constant like the 1m bar size), NOT fitted per-sample.
BIG = 0.35

BARS_SQL = f"""
SELECT timestamp,
       first(price) o, max(price) h, min(price) l, last(price) c,
       sum(size) vol,
       sum(price * size) pv,
       sum(size * CASE WHEN side='BUY' THEN 1 ELSE -1 END) delta,
       avg(size) avg_size,
       max(size) max_size,
       sum(CASE WHEN size >= {BIG} THEN size ELSE 0 END) vol_big,
       sum(CASE WHEN size >= {BIG} AND side='BUY' THEN size
                WHEN size >= {BIG} THEN -size
                ELSE 0 END) delta_big,
       count() n
FROM agg_trades
SAMPLE BY 1m
"""

FEATURES = [
    # returns / momentum
    "ret1", "mom_5", "mom_15", "mom_60",
    # volatility / bar geometry / regime
    "volat_15", "vol_ratio", "range_norm", "close_pos",
    # order-flow imbalance (ratios in [-1, 1])
    "delta_norm", "imb_5", "imb_15", "imb_60",
    # trade-size structure
    "big_share", "big_delta_norm", "max_share", "avg_size_rel",
    # activity vs own 1h norm
    "n_rel", "vol_rel",
    # price vs rolling VWAP
    "vwap_dist",
    # clock
    "hod_sin", "hod_cos", "dow",
]


def load_bars() -> pd.DataFrame:
    """1-minute order-flow bars from QuestDB as a DataFrame (CSV via /exp)."""
    url = QUESTDB_EXP + "?" + urllib.parse.urlencode({"query": BARS_SQL})
    raw = urllib.request.urlopen(url, timeout=120).read().decode()
    df = pd.read_csv(io.StringIO(raw), parse_dates=["timestamp"])
    return df.sort_values("timestamp").reset_index(drop=True)


def build_dataset(horizon: int = 15, cost: float = 0.0015, k: float = 0.5) -> pd.DataFrame:
    """bars + features + label.

    horizon in bars (minutes); cost = round-trip cost floor of the dead zone;
    k = how many local sigmas on top of cost a move must clear to be a label.
    """
    df = load_bars()
    c, h, l = df["c"], df["h"], df["l"]

    # --- returns / momentum ---
    df["ret1"] = c.pct_change(fill_method=None)
    df["mom_5"] = c.pct_change(5, fill_method=None)
    df["mom_15"] = c.pct_change(15, fill_method=None)
    df["mom_60"] = c.pct_change(60, fill_method=None)

    # --- volatility / bar geometry ---
    df["volat_15"] = df["ret1"].rolling(15).std()
    volat_60 = df["ret1"].rolling(60).std()
    df["vol_ratio"] = df["volat_15"] / volat_60.replace(0, np.nan)  # >1 = vol expanding
    bar_range = h - l
    df["range_norm"] = bar_range / c
    # where inside the bar did we close (0=low, 1=high); h==l -> neutral 0.5
    df["close_pos"] = ((c - l) / bar_range.replace(0, np.nan)).fillna(0.5)

    # --- order-flow imbalance: ratios, not raw BTC sums ---
    # vol > 0 always: SAMPLE BY emits no row for an empty minute
    df["delta_norm"] = df["delta"] / df["vol"]
    for w in (5, 15, 60):
        df[f"imb_{w}"] = df["delta"].rolling(w).sum() / df["vol"].rolling(w).sum()

    # --- trade-size structure: proxy for large-player activity ---
    df["big_share"] = df["vol_big"] / df["vol"]        # share of volume from big prints
    df["big_delta_norm"] = df["delta_big"] / df["vol"]  # ...and which way it leaned
    df["max_share"] = df["max_size"] / df["vol"]        # one whale dominating the bar?
    df["avg_size_rel"] = df["avg_size"] / df["avg_size"].rolling(60).mean() - 1.0

    # --- activity vs its own 1h norm (raw counts drift across months) ---
    df["n_rel"] = df["n"] / df["n"].rolling(60).mean() - 1.0
    df["vol_rel"] = df["vol"] / df["vol"].rolling(60).mean() - 1.0

    # --- price vs rolling 1h VWAP, in return units ---
    vwap60 = df["pv"].rolling(60).sum() / df["vol"].rolling(60).sum()
    df["vwap_dist"] = c / vwap60 - 1.0

    # --- clock (UTC) ---
    hod = df["timestamp"].dt.hour + df["timestamp"].dt.minute / 60.0
    df["hod_sin"] = np.sin(2 * np.pi * hod / 24.0)
    df["hod_cos"] = np.cos(2 * np.pi * hod / 24.0)
    df["dow"] = df["timestamp"].dt.dayofweek.astype(float)

    # --- label: forward return vs vol-scaled dead zone ---
    df["fwd_ret"] = c.shift(-horizon) / c - 1.0        # the ONLY look-forward
    sigma_h = df["volat_15"] * np.sqrt(horizon)        # random-walk scaling: 1m vol -> H bars
    df["thr"] = cost + k * sigma_h
    df["label"] = 0
    df.loc[df["fwd_ret"] > df["thr"], "label"] = 1
    df.loc[df["fwd_ret"] < -df["thr"], "label"] = -1

    # drop warm-up rows (rolling NaNs) and the last `horizon` rows (no future)
    df = df.dropna(subset=FEATURES + ["fwd_ret"]).reset_index(drop=True)
    return df


if __name__ == "__main__":
    if len(sys.argv) > 1:
        H = int(sys.argv[1])
        cost = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0015
        k = float(sys.argv[3]) if len(sys.argv) > 3 else 0.5
        d = build_dataset(horizon=H, cost=cost, k=k)
        print(f"dataset: {len(d):,} rows | {len(FEATURES)} features | H={H}m | cost={cost} | k={k}")
        print(f"range: {d['timestamp'].min()} .. {d['timestamp'].max()}")
        print(f"median dead zone: {d['thr'].median() * 1e4:.1f} bps")
        print("\nlabel counts (-1 down / 0 flat / +1 up):")
        print(d["label"].value_counts().sort_index().to_string())
        print(f"flat fraction: {(d['label'] == 0).mean():.1%}")
    else:
        # class-balance sweep across the horizon grid — the step-2 sanity check
        for H in (5, 15, 60):
            d = build_dataset(horizon=H)
            lab = d["label"]
            print(f"H={H:3d}m | rows {len(d):,} | flat {(lab == 0).mean():5.1%} "
                  f"| up {(lab == 1).mean():5.1%} | down {(lab == -1).mean():5.1%} "
                  f"| median zone {d['thr'].median() * 1e4:5.1f} bps")
