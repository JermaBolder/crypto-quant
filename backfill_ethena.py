"""Ethena chapter, step 1: sUSDe realized yield history -> QuestDB.

WHY this dataset: sUSDe is the institutional implementation of EXACTLY the
strategy the carry chapter measured (short perp + long spot, harvest funding).
Ethena publishes what it actually paid stakers; DefiLlama archives it daily.
Comparing our measured Binance carry against their realized APY is the honest
validation study — does the live implementation earn what the study predicts?

Source: DefiLlama free API (no key), the canonical Ethena staking pool
(project=ethena-usde, symbol=SUSDE, Ethereum, ~$1.6B TVL — found by scanning
yields.llama.fi/pools and VERIFIED live, not assumed).

Format facts (verified on the real response):
  - one point per UTC day since 2024-02-16, no nulls, no duplicate days;
  - timestamps are snapshot times WITH jitter (~23:01Z usually) - stored RAW,
    the analysis layer floors to the day;
  - the LAST point is today's partial-day snapshot which DefiLlama replaces
    later -> we ingest COMPLETE DAYS ONLY (ts < today 00:00 UTC), same spirit
    as backfill.py's "dumps lag ~1-2 days" guard. Re-runs append only points
    newer than what the table already has: idempotent, dedup-free.

Usage:  python backfill_ethena.py        # incremental sync (safe to re-run)
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

SUSDE_DDL = """CREATE TABLE IF NOT EXISTS susde_yield (
  pool SYMBOL, apy DOUBLE, tvl_usd DOUBLE, timestamp TIMESTAMP
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


def select_new(points: list[dict], since_ns: int, cutoff_ns: int) -> list[dict]:
    """Points strictly after what we have and strictly before today (UTC):
    complete days only - today's partial snapshot gets replaced upstream."""
    return [p for p in points if since_ns < iso_to_ns(p["timestamp"]) < cutoff_ns]


def latest_ns() -> int:
    """Newest stored point, 0 if the table is empty."""
    q = "select max(timestamp) from susde_yield"
    url = f"{QUESTDB_HTTP}/exec?query={urllib.parse.quote(q)}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        rows = json.loads(resp.read())["dataset"]
    if not rows or rows[0][0] is None:
        return 0
    return iso_to_ns(rows[0][0])


def main() -> None:
    ensure_table(SUSDE_DDL)
    raw = urllib.request.urlopen(CHART_URL, timeout=60).read()
    points = json.loads(raw)["data"]
    cutoff = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    new = select_new(points, latest_ns(), int(cutoff.timestamp()) * 1_000_000_000)
    if not new:
        print(f"susde_yield: fetched {len(points)} points, nothing new — up to date")
        return
    _post([point_to_line(p) for p in new])
    print(f"susde_yield: fetched {len(points)} points, ingested {len(new)} new "
          f"({new[0]['timestamp'][:10]} .. {new[-1]['timestamp'][:10]})")


if __name__ == "__main__":
    main()
