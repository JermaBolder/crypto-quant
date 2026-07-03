"""Phase 1 consumer: Redis Stream -> QuestDB, via a consumer group.

A consumer group gives at-least-once delivery: Redis tracks which entries the
group has handed out, you ACK an entry only once it's safely in QuestDB, and
any un-acked entries (e.g. the process died mid-write) get re-delivered later.
That resilience is the whole reason we put a bus in the middle.

Usage:
  python consumer_questdb.py       # daemon (Ctrl-C to stop)
  python consumer_questdb.py 10    # run ~10s then stop
"""
from __future__ import annotations

import asyncio
import sys
import time

import redis.asyncio as redis
from redis.exceptions import ResponseError

import qdb_sink
from config import REDIS_HOST, REDIS_PORT

STREAM = "trades"
GROUP = "cg_questdb"
CONSUMER = "c1"


async def ensure_group(r: redis.Redis) -> None:
    try:
        # start the group at '0' = beginning of stream, so it also picks up
        # entries produced before the group existed. mkstream: create if absent.
        await r.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
        print(f"created consumer group '{GROUP}'")
    except ResponseError as e:
        if "BUSYGROUP" not in str(e):   # group already exists -> fine
            raise


async def run(max_seconds: float | None = None, batch: int = 200,
              block_ms: int = 2000) -> None:
    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    await r.ping()
    await ensure_group(r)
    print(f"consumer: redis '{STREAM}' (group {GROUP}) -> QuestDB")

    total = 0
    start = time.monotonic()
    while True:
        # '>' = only entries never delivered to this group. BLOCK waits up to
        # block_ms for new ones instead of busy-spinning when the stream is idle.
        resp = await r.xreadgroup(GROUP, CONSUMER, {STREAM: ">"},
                                  count=batch, block=block_ms)
        if resp:
            _stream, entries = resp[0]
            lines, ids = [], []
            for entry_id, f in entries:
                lines.append(qdb_sink.line(
                    f["symbol"], f["side"], float(f["price"]),
                    float(f["size"]), int(f["trade_id"]), int(f["ts_ns"]),
                ))
                ids.append(entry_id)
            try:
                await asyncio.to_thread(qdb_sink.flush, lines)   # blocking I/O off the loop
                await r.xack(STREAM, GROUP, *ids)                # ACK only AFTER the DB write
                total += len(ids)
                print(f"[consumer] wrote {total} -> QuestDB")
            except Exception as e:  # noqa: BLE001
                # DB write failed: do NOT ack -> entries stay pending, get re-delivered
                print(f"[consumer] flush failed, NOT acking {len(ids)} entries: {e}")

        if max_seconds is not None and (time.monotonic() - start) >= max_seconds:
            print(f"[consumer] done: {total} rows in ~{int(time.monotonic() - start)}s")
            break

    await r.aclose()


if __name__ == "__main__":
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else None
    try:
        asyncio.run(run(max_seconds=secs))
    except KeyboardInterrupt:
        print("\nstopped.")
