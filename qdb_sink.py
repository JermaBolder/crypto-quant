"""QuestDB sink: write trades via InfluxDB line protocol over HTTP.

Stdlib only - QuestDB's /write endpoint speaks line protocol, so no DB driver
needed (and you see the wire format). Shared component: the consumer uses it;
the legacy ingest.py still has its own inline copy until we retire it.
"""
from __future__ import annotations

import urllib.request

from config import QUESTDB_HTTP

QUESTDB_WRITE = f"{QUESTDB_HTTP}/write"
TABLE = "trades"


def line(symbol: str, side: str, price: float, size: float,
         trade_id: int, ts_ns: int) -> str:
    # measurement,tags fields timestamp_ns ; :.8f avoids scientific notation
    return (
        f"{TABLE},symbol={symbol},side={side} "
        f"price={price:.8f},size={size:.8f},id={trade_id}i {ts_ns}"
    )


def flush(lines: list[str]) -> None:
    body = ("\n".join(lines) + "\n").encode()
    req = urllib.request.Request(QUESTDB_WRITE, data=body, method="POST")
    urllib.request.urlopen(req, timeout=10)  # 204 on success, raises on 4xx
