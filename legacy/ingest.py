"""Phase 0 ingest: live crypto trades -> QuestDB (via ILP over HTTP).

Design note - pluggable source (the data-source-agnostic engine we agreed on):
  TradeSource is the abstraction; BinanceTradeSource is one implementation.
  The DB sink below never knows which exchange (or, later, FX feed) produced
  a trade. Swapping Binance -> Bybit, or pointing at your FX data, touches
  ONLY the source class. The engine doesn't care.

No `questdb` client needed: we POST InfluxDB line-protocol straight to
QuestDB's HTTP endpoint with the standard library. Bonus - you see the
exact wire format. QuestDB auto-creates the `trades` table on first write
(schema-on-write) and uses our timestamp as the designated timestamp.

Usage:
  python ingest.py        # daemon: run forever (Ctrl-C to stop)
  python ingest.py 12     # bounded test run: ingest for ~12s, then stop
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator

import websockets

QUESTDB_WRITE = "http://127.0.0.1:9000/write"
TABLE = "trades"


@dataclass
class Trade:
    ts_ns: int     # event time, nanoseconds
    symbol: str
    price: float
    size: float
    side: str      # "BUY" / "SELL" = aggressor side (order flow)
    trade_id: int


class TradeSource(ABC):
    """A source yields a stream of normalized Trades, forever."""

    @abstractmethod
    def stream(self) -> AsyncIterator[Trade]:
        ...


class BinanceTradeSource(TradeSource):
    def __init__(self, symbol: str = "btcusdt") -> None:
        self.symbol = symbol.lower()
        self.url = f"wss://stream.binance.com:9443/ws/{self.symbol}@trade"

    async def stream(self) -> AsyncIterator[Trade]:
        # auto-reconnect: an ingest daemon must survive websocket drops
        while True:
            try:
                async with websockets.connect(self.url, ping_interval=20) as ws:
                    async for raw in ws:
                        t = json.loads(raw)
                        yield Trade(
                            ts_ns=int(t["T"]) * 1_000_000,        # ms -> ns
                            symbol=t["s"],
                            price=float(t["p"]),
                            size=float(t["q"]),
                            side="SELL" if t["m"] else "BUY",     # aggressor
                            trade_id=int(t["t"]),
                        )
            except Exception as e:  # noqa: BLE001
                print(f"[source] reconnecting after error: {e}")
                await asyncio.sleep(2)


def _line(t: Trade) -> str:
    # InfluxDB line protocol:  measurement,tags fields timestamp_ns
    # :.8f avoids scientific notation, which the ILP parser rejects.
    return (
        f"{TABLE},symbol={t.symbol},side={t.side} "
        f"price={t.price:.8f},size={t.size:.8f},id={t.trade_id}i "
        f"{t.ts_ns}"
    )


def _flush(lines: list[str]) -> None:
    body = ("\n".join(lines) + "\n").encode()
    req = urllib.request.Request(QUESTDB_WRITE, data=body, method="POST")
    urllib.request.urlopen(req, timeout=10)  # 204 on success, raises on 4xx


async def run(source: TradeSource, batch: int = 50, flush_secs: float = 1.0,
              max_seconds: float | None = None) -> None:
    buf: list[str] = []
    total = 0
    start = time.monotonic()
    last_flush = start
    last_report = start

    def do_flush() -> None:
        nonlocal total, buf
        if not buf:
            return
        try:
            _flush(buf)
            total += len(buf)
        except Exception as e:  # noqa: BLE001
            print(f"[sink] flush failed ({e}); dropping {len(buf)} lines")
        buf = []

    async for tr in source.stream():
        buf.append(_line(tr))
        now = time.monotonic()
        if len(buf) >= batch or (now - last_flush) >= flush_secs:
            await asyncio.to_thread(do_flush)
            last_flush = now
        if now - last_report >= 5:
            print(f"[ingest] {total:>6} trades -> QuestDB   "
                  f"(last: {tr.symbol} {tr.side} {tr.size:.5f} @ {tr.price:,.2f})")
            last_report = now
        if max_seconds is not None and (now - start) >= max_seconds:
            await asyncio.to_thread(do_flush)
            print(f"[ingest] done: {total} trades in ~{int(now - start)}s")
            return


if __name__ == "__main__":
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else None
    src = BinanceTradeSource("btcusdt")
    mode = f"{secs:.0f}s test run" if secs else "daemon (Ctrl-C to stop)"
    print(f"ingesting BTCUSDT trades -> QuestDB  [{mode}]")
    try:
        asyncio.run(run(src, max_seconds=secs))
    except KeyboardInterrupt:
        print("\nstopped.")
