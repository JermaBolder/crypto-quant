"""Phase 2 bridge: a SECOND consumer on the same stream — rolling order-flow delta.

Fan-out demo. This reads the same 'trades' stream but under its OWN consumer
group ('cg_metrics'). Separate groups EACH receive every message independently,
so the QuestDB writer and this metrics consumer both process all trades in
parallel, neither blocking the other. (Consumers within ONE group split the
work; separate GROUPS each get a full copy.)

Signal: rolling delta = buy volume - sell volume over a time window — the
order-flow 'delta' from FX, live off the bus. Negative = sellers leaning in.

Usage:
  python consumer_metrics.py        # daemon
  python consumer_metrics.py 20     # ~20s then stop
"""
from __future__ import annotations

import asyncio
import sys
import time
from collections import deque

import redis.asyncio as redis
from redis.exceptions import ResponseError

STREAM = "trades"
GROUP = "cg_metrics"
CONSUMER = "m1"
WINDOW_S = 60.0   # rolling window length, seconds


async def ensure_group(r: redis.Redis) -> None:
    try:
        # '$' = start at NEW messages only. A live rolling metric wants the
        # current flow, not a replay of all history (unlike the DB writer).
        await r.xgroup_create(STREAM, GROUP, id="$", mkstream=True)
        print(f"created consumer group '{GROUP}' (live, new messages only)")
    except ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


async def run(max_seconds: float | None = None) -> None:
    r = redis.Redis(host="127.0.0.1", port=6379, decode_responses=True)
    await r.ping()
    await ensure_group(r)
    print(f"metrics: rolling {WINDOW_S:.0f}s order-flow delta on '{STREAM}'")

    window = deque()      # (event_ts_seconds, side, size)
    start = time.monotonic()
    last_print = 0.0

    while True:
        resp = await r.xreadgroup(GROUP, CONSUMER, {STREAM: ">"}, count=500, block=1000)
        if resp:
            _stream, entries = resp[0]
            ids = []
            for entry_id, f in entries:
                window.append((int(f["ts_ns"]) / 1e9, f["side"], float(f["size"])))
                ids.append(entry_id)
            await r.xack(STREAM, GROUP, *ids)

        # evict trades older than the window, anchored on the latest EVENT time
        # (market time, not wall-clock — a quiet stream shouldn't blank the window)
        if window:
            cutoff = window[-1][0] - WINDOW_S
            while window and window[0][0] < cutoff:
                window.popleft()

        now = time.monotonic()
        if now - last_print >= 1.0 and window:
            # recompute exactly from the window each second: N is small, so this
            # is cheap and drift-free — no fragile running sums to keep in step
            buy = sum(sz for _t, sd, sz in window if sd == "BUY")
            sell = sum(sz for _t, sd, sz in window if sd == "SELL")
            delta = buy - sell
            lean = "sellers" if delta < 0 else "buyers"
            print(f"[metrics] {WINDOW_S:.0f}s  buy={buy:.4f}  sell={sell:.4f}  "
                  f"delta={delta:+.4f} BTC -> {lean}  (n={len(window)})")
            last_print = now

        if max_seconds is not None and (now - start) >= max_seconds:
            print(f"[metrics] done in ~{int(now - start)}s")
            break

    await r.aclose()


if __name__ == "__main__":
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else None
    try:
        asyncio.run(run(max_seconds=secs))
    except KeyboardInterrupt:
        print("\nstopped.")
