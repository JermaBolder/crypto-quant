"""Ethena chapter verdict: does the live institutional implementation (sUSDe)
earn what our measured funding predicts — and what explains the gap?

This is an ATTRIBUTION study, not a strategy search: nothing is tuned, nothing
is fit, so there is no OOS split and no theta to pick. What replaces the stop
rule is a CLOSURE rule, committed before the numbers were computed:
  1. Measure the two candidates free data can measure:
       (a) hedge choice — ETH funding vs BTC funding on the same window;
       (b) concentration — yield accrues on ALL of USDe's backing but is paid
           only to the staked share, so circulating/staked-TVL multiplies the
           stakers' rate.
  2. Report where realized sUSDe APY falls against the predicted band
     [BTC-hedge, ETH-hedge] x multiplier.
  3. The RESIDUAL is reported with its candidate owners (liquid-stables/
     T-bill yield on the unhedged share, reserve-fund policy, multi-exchange
     spread, the actual hedge mix) — it is NOT chased with more ingestion.
     The chapter closes with this table either way.

Timing notes: sUSDe snapshots are stamped ~23:01Z of the day they describe;
supply points at the day's midnight; funding settles 00/08/16Z. Everything is
floored to the UTC day — a one-day smear, immaterial for series that move a
few percent per week, and the same floor is applied to every input. Funding
days without exactly 3 settlements (exchange incidents, window edges) are
dropped and COUNTED, per house rule.
"""
from __future__ import annotations

import pandas as pd

from carry import _export

DPY = 365            # daily points per year
SYMBOLS = {"btc": "BTCUSDT", "eth": "ETHUSDT"}


def _load(sql: str) -> pd.DataFrame:
    """QuestDB exports ISO-with-Z -> tz-aware UTC; day-floor joins and Period
    quarters want naive-UTC, so strip the tz once, at the boundary."""
    df = _export(sql)
    df["timestamp"] = df["timestamp"].dt.tz_localize(None)
    return df


def load_susde() -> pd.DataFrame:
    return _load("SELECT timestamp, apy, tvl_usd FROM susde_yield")


def load_supply() -> pd.DataFrame:
    return _load("SELECT timestamp, circulating FROM usde_supply")


def load_funding(symbol: str) -> pd.DataFrame:
    return _load("SELECT timestamp, rate, interval_hours FROM funding "
                 f"WHERE symbol = '{symbol}'")


def daily_funding(ts: pd.Series, rate: pd.Series) -> tuple[pd.Series, int]:
    """8h settlements -> one summed rate per UTC day (a fraction). Days
    without exactly 3 settlements are dropped and counted, not papered over."""
    day = ts.dt.floor("D")
    g = rate.groupby(day)
    total, n = g.sum(), g.count()
    return total[n == 3], int((n != 3).sum())


def build_panel(susde: pd.DataFrame, supply: pd.DataFrame,
                funding: dict[str, pd.Series]) -> pd.DataFrame:
    """Inner-join everything on the UTC day; one row = one fully-observed day.
    mult = circulating / staked TVL: the stakers' yield multiplier. pred_<s> =
    what stakers would earn (%/yr) if 100% of the backing were an <s> perp
    hedge — the per-day product keeps the funding-multiplier covariance."""
    df = (susde.assign(day=susde["timestamp"].dt.floor("D"))
          .set_index("day")[["apy", "tvl_usd"]])
    sup = (supply.assign(day=supply["timestamp"].dt.floor("D"))
           .set_index("day")["circulating"])
    df = df.join(sup, how="inner")
    for name, s in funding.items():
        df = df.join(s.rename(f"fund_{name}"), how="inner")
    df = df.dropna()
    df["mult"] = df["circulating"] / df["tvl_usd"]
    for name in funding:
        df[f"pred_{name}"] = df[f"fund_{name}"] * df["mult"] * DPY * 100
    return df


def quarter_table(df: pd.DataFrame) -> pd.DataFrame:
    """Per-quarter means: the time structure the single-number verdict hides."""
    q = df.groupby(df.index.to_period("Q"))
    out = pd.DataFrame({
        "susde_apy": q["apy"].mean(),
        "mult": q["mult"].mean(),
        "btc_gross": q["fund_btc"].mean() * DPY * 100,
        "eth_gross": q["fund_eth"].mean() * DPY * 100,
        "pred_btc": q["pred_btc"].mean(),
        "pred_eth": q["pred_eth"].mean(),
    })
    out["days"] = q.size()
    return out


def main() -> None:
    funding: dict[str, pd.Series] = {}
    dropped: dict[str, int] = {}
    for name, symbol in SYMBOLS.items():
        f = load_funding(symbol)
        assert (f["interval_hours"] == 8).all(), f"8h assumption violated for {symbol}"
        funding[name], dropped[name] = daily_funding(f["timestamp"], f["rate"])

    df = build_panel(load_susde(), load_supply(), funding)
    print(f"ethena eval: {len(df):,} joint days | {df.index.min():%Y-%m-%d} .. "
          f"{df.index.max():%Y-%m-%d} | dropped funding days: "
          + ", ".join(f"{k} {v}" for k, v in dropped.items()))

    apy = df["apy"]
    print("\n— realized (what Ethena actually paid sUSDe stakers) —")
    print(f"sUSDe APY %/yr: mean {apy.mean():.2f}  median {apy.median():.2f}  "
          f"p5 {apy.quantile(0.05):.2f}  p95 {apy.quantile(0.95):.2f}")

    btc_g = df["fund_btc"].mean() * DPY * 100
    eth_g = df["fund_eth"].mean() * DPY * 100
    share = 1 / df["mult"]
    print("\n— measured candidates —")
    print(f"(a) hedge choice, funding gross %/yr: BTC {btc_g:.2f}  ETH {eth_g:.2f} "
          f"(ETH-BTC = {eth_g - btc_g:+.2f} pp)")
    print(f"(b) concentration: staked share mean {share.mean():.1%} "
          f"(range {share.min():.1%}..{share.max():.1%}) -> "
          f"multiplier mean {df['mult'].mean():.2f}x")
    pred_btc, pred_eth = df["pred_btc"].mean(), df["pred_eth"].mean()
    print(f"predicted stakers' APY, 100% hedge x multiplier, %/yr: "
          f"BTC {pred_btc:.2f}  ETH {pred_eth:.2f}")
    smooth = df[["apy", "pred_eth"]].rolling(7).mean().dropna()
    print(f"regime tracking: corr(7d sUSDe APY, 7d ETH prediction) = "
          f"{smooth['apy'].corr(smooth['pred_eth']):+.2f}")

    qt = quarter_table(df)
    print("\n— by quarter (means, %/yr) —")
    print("quarter   sUSDe   mult   BTC-gross  ETH-gross  pred-BTC  pred-ETH  days")
    for idx, r in qt.iterrows():
        print(f"{str(idx):8s} {r['susde_apy']:6.2f} {r['mult']:5.2f}x "
              f"{r['btc_gross']:9.2f} {r['eth_gross']:10.2f} "
              f"{r['pred_btc']:9.2f} {r['pred_eth']:9.2f}  {int(r['days']):4d}")

    lo, hi = min(pred_btc, pred_eth), max(pred_btc, pred_eth)
    realized = apy.mean()
    print("\n== VERDICT " + "=" * 55)
    print(f"realized {realized:.2f} %/yr vs predicted band [{lo:.2f}, {hi:.2f}]")
    if realized > hi:
        print(f"UNDER-EXPLAINED: realized exceeds the band by {realized - hi:+.2f} pp.")
        print("The measured candidates are not the whole story; the excess is owned")
        print("by the unmeasured residual (stables/T-bill yield, multi-exchange,")
        print("hedge mix). Reported, not chased — closure rule ends the chapter.")
    elif realized < lo:
        print(f"OVER-PREDICTED: realized sits {lo - realized:.2f} pp BELOW the band's")
        print("floor. The naive '100% perp hedge x staked multiplier' model earns")
        print("MORE than Ethena pays: the difference is the cost of reality — part")
        print("of the backing is liquid stables/LSTs (not levered funding), and the")
        print("reserve fund keeps a buffer. Reported, not chased — chapter closed.")
    else:
        print("EXPLAINED: realized falls inside the band — hedge choice plus the")
        print("staked-share multiplier account for the gap without any residual")
        print("story. Chapter closed.")


if __name__ == "__main__":
    main()
