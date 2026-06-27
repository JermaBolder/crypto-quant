"""Phase 1 producer: WS trades -> Redis Stream (the message bus).

WHY put a bus between source and DB instead of writing straight to QuestDB?
Decoupling. The producer's only job is to get events onto the bus fast and
reliably. Whoever consumes them (DB writer, live metrics, later ML features)
reads at their own pace, can crash and resume from where they left off, and we
can add more consumers without touching this file.

A Redis Stream is an append-only log inside Redis: XADD appends an entry with
an auto ID (millis-seq) and field=value pairs; consumers read by ID and the
data stays put after reading (unlike a plain queue).

Usage:
  python producer.py        # daemon (Ctrl-C to stop)
  python producer.py 8      # 8-second test run, then stop
"""
from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import asdict

import redis.asyncio as redis

from sources import BinanceTradeSource

STREAM = "trades"
MAXLEN = 100_000  # cap stream length so Redis can't grow unbounded (OOM guard)


async def run(symbol: str = "btcusdt", max_seconds: float | None = None) -> None:
    r = redis.Redis(host="127.0.0.1", port=6379, decode_responses=True)
    # fail fast if the bus isn't reachable, instead of silently dropping later
    await r.ping()

    src = BinanceTradeSource(symbol)
    mode = f"{max_seconds:.0f}s test" if max_seconds else "daemon"
    print(f"producer: {symbol.upper()} -> redis stream '{STREAM}' [{mode}]")

    n = 0
    start = time.monotonic()
    async for tr in src.stream():
        # one trade -> one stream entry; fields must be flat str/number pairs
        fields = {k: str(v) for k, v in asdict(tr).items()}
        try:
            # maxlen + approximate: trim to ~MAXLEN entries; '~' is far cheaper
            # than exact trimming because Redis can drop whole macro-nodes at once
            await r.xadd(STREAM, fields, maxlen=MAXLEN, approximate=True)
            n += 1
        except Exception as e:  # noqa: BLE001
            # Redis down/unreachable: we log and DROP this trade. THIS is the spot
            # where a real system needs a buffer/backpressure policy - not pretending
            # it's solved.
            print(f"[producer] XADD failed, dropping trade: {e}")

        now = time.monotonic()
        if n and n % 200 == 0:
            print(f"[producer] published {n} | stream len ~{await r.xlen(STREAM)}")
        if max_seconds is not None and (now - start) >= max_seconds:
            print(f"[producer] done: {n} published in ~{int(now - start)}s "
                  f"| stream len ~{await r.xlen(STREAM)}")
            break

    await r.aclose()


if __name__ == "__main__":
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else None
    try:
        asyncio.run(run(max_seconds=secs))
    except KeyboardInterrupt:
        print("\nstopped.")
