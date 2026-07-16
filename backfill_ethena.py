"""Ethena chapter data layer: sUSDe realized yield + USDe supply -> QuestDB.

WHY these datasets: sUSDe is the institutional implementation of EXACTLY the
strategy the carry chapter measured (short perp + long spot, harvest funding).
Ethena publishes what it actually paid stakers; DefiLlama archives it daily.
Comparing our measured Binance carry against their realized APY is the honest
validation study — does the live implementation earn what the study predicts?
USDe circulating supply is the chapter's second measured input: yield is
generated on the WHOLE backing but paid only to the staked share, so
supply / staked TVL is the stakers' yield multiplier.

Source: DefiLlama free APIs (no key):
  - yields.llama.fi/chart/<pool>: the canonical Ethena staking pool
    (project=ethena-usde, symbol=SUSDE, Ethereum, ~$1.6B TVL — found by
    scanning yields.llama.fi/pools and VERIFIED live, not assumed);
  - stablecoins.llama.fi/stablecoin/146: USDe circulating history
    (id 146 = "Ethena USDe", ~$4.0B — verified against /stablecoins).

Format facts (verified on the real responses):
  - yield: one point per UTC day since 2024-02-16, no nulls, no duplicate
    days; timestamps are ISO snapshot times WITH jitter (~23:01Z usually) -
    stored RAW, the analysis layer floors to the day;
  - supply: one point per UTC day since 2023-12-11, stamped at midnight UTC
    in epoch SECONDS (not ISO); value = circulating.peggedUSD;
  - in BOTH, the last point is today's live snapshot that DefiLlama updates
    in place -> we ingest COMPLETE DAYS ONLY (ts < today 00:00 UTC), same
    spirit as backfill.py's "dumps lag ~1-2 days" guard. Re-runs append only
    points newer than what the table already has: idempotent, dedup-free.

Usage:  python backfill_ethena.py        # incremental sync of both (re-run safe)
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import UTC, datetime

from backfill import _post
from backfill_futures import ensure_table
from config import QUESTDB_HTTP

# The native Ethena staking pool on DefiLlama. An operational constant like
# BIG in dataset.py: verified once against /pools (project ethena-usde,
# symbol SUSDE, chain Ethereum), not looked up on every run.
POOL_ID = "66985a81-9c51-46ca-9977-42b4fe7bc6df"
CHART_URL = f"https://yields.llama.fi/chart/{POOL_ID}"
USDE_ID = 146   # "Ethena USDe" on stablecoins.llama.fi — verified, not assumed
SUPPLY_URL = f"https://stablecoins.llama.fi/stablecoin/{USDE_ID}"

SUSDE_DDL = """CREATE TABLE IF NOT EXISTS susde_yield (
  pool SYMBOL, apy DOUBLE, tvl_usd DOUBLE, timestamp TIMESTAMP
) TIMESTAMP(timestamp) PARTITION BY YEAR"""

USDE_DDL = """CREATE TABLE IF NOT EXISTS usde_supply (
  asset SYMBOL, circulating DOUBLE, timestamp TIMESTAMP
) TIMESTAMP(timestamp) PARTITION BY YEAR"""


def iso_to_ns(iso: str) -> int:
    """DefiLlama ISO snapshot ('2024-02-16T23:01:19.228Z') -> epoch ns."""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return round(dt.timestamp() * 1000) * 1_000_000   # via ms: exact in float64


def point_to_line(p: dict) -> str:
    """One chart point -> ILP line (apy in percent, as published)."""
    return (
        f"susde_yield,pool=ethena-susde "
        f"apy={float(p['apy']):.6f},tvl_usd={float(p['tvlUsd']):.2f} "
        f"{iso_to_ns(p['timestamp'])}"
    )


def day_point_ns(p: dict) -> int:
    """Supply points stamp UTC midnight in epoch SECONDS (yield points are ISO)."""
    return int(p["date"]) * 1_000_000_000


def iso_point_ns(p: dict) -> int:
    return iso_to_ns(p["timestamp"])


def supply_to_line(p: dict) -> str:
    """One supply point -> ILP line (circulating USD)."""
    return (
        f"usde_supply,asset=usde "
        f"circulating={float(p['circulating']['peggedUSD']):.2f} {day_point_ns(p)}"
    )


def select_new(points: list[dict], since_ns: int, cutoff_ns: int,
               ts_of=iso_point_ns) -> list[dict]:
    """Points strictly after what we have and strictly before today (UTC):
    complete days only - today's live snapshot gets replaced upstream."""
    return [p for p in points if since_ns < ts_of(p) < cutoff_ns]


def latest_ns(table: str) -> int:
    """Newest stored point, 0 if the table is empty."""
    q = f"select max(timestamp) from {table}"
    url = f"{QUESTDB_HTTP}/exec?query={urllib.parse.quote(q)}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        rows = json.loads(resp.read())["dataset"]
    if not rows or rows[0][0] is None:
        return 0
    return iso_to_ns(rows[0][0])


def today_cutoff_ns() -> int:
    cutoff = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(cutoff.timestamp()) * 1_000_000_000


def sync(table: str, ddl: str, url: str, data_key: str, to_line, ts_of,
         day_of) -> None:
    """One incremental table sync; day_of renders a point's day for the log."""
    ensure_table(ddl)
    raw = urllib.request.urlopen(url, timeout=60).read()
    points = json.loads(raw)[data_key]
    new = select_new(points, latest_ns(table), today_cutoff_ns(), ts_of)
    if not new:
        print(f"{table}: fetched {len(points)} points, nothing new — up to date")
        return
    _post([to_line(p) for p in new])
    print(f"{table}: fetched {len(points)} points, ingested {len(new)} new "
          f"({day_of(new[0])} .. {day_of(new[-1])})")


def main() -> None:
    sync("susde_yield", SUSDE_DDL, CHART_URL, "data", point_to_line,
         iso_point_ns, lambda p: p["timestamp"][:10])
    sync("usde_supply", USDE_DDL, SUPPLY_URL, "tokens", supply_to_line,
         day_point_ns, lambda p: datetime.fromtimestamp(p["date"], UTC).date().isoformat())


if __name__ == "__main__":
    main()
