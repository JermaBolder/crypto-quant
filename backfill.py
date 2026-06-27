"""Phase 2 backfill: historical Binance aggTrades -> QuestDB table 'agg_trades'.

Source: data.binance.vision daily dumps (no rate limits, bulk history). Each
daily CSV is ~1.4M rows for BTCUSDT, so we STREAM it (never hold the whole file
in memory) and POST to QuestDB in ILP batches.

Format gotchas (VERIFIED on a real file, not assumed):
  columns: aggId, price, qty, firstId, lastId, transactTime, isBuyerMaker, isBestMatch
  - transactTime is in MICROSECONDS here (the live websocket gives ms) -> *1000 = ns
  - isBuyerMaker=true means the buyer was the maker, so the aggressor is the
    SELLER -> SELL.

Usage:
  python backfill.py 2026-06-24                 # one explicit day
  python backfill.py 2026-06-22 2026-06-23      # several days
  python backfill.py --days 3                    # last 3 available days

NOT idempotent: re-running a day double-inserts it. Drop/recreate the table to
redo cleanly. (We'll add proper dedup if we keep extending the backfill.)
"""
from __future__ import annotations

import csv
import io
import sys
import urllib.request
import zipfile
from datetime import date, timedelta

SYMBOL = "BTCUSDT"
TABLE = "agg_trades"
QUESTDB_WRITE = "http://127.0.0.1:9000/write"
BASE = "https://data.binance.vision/data/spot/daily/aggTrades"
BATCH = 10_000


def _post(lines: list[str]) -> None:
    body = ("\n".join(lines) + "\n").encode()
    req = urllib.request.Request(QUESTDB_WRITE, data=body, method="POST")
    urllib.request.urlopen(req, timeout=30)  # raises on non-2xx


def ingest_day(d: date) -> int:
    url = f"{BASE}/{SYMBOL}/{SYMBOL}-aggTrades-{d.isoformat()}.zip"
    print(f"  {d}: downloading ...", flush=True)
    try:
        raw = urllib.request.urlopen(url, timeout=180).read()
    except Exception as e:  # noqa: BLE001
        print(f"  {d}: !! download failed ({e}) — skipping")
        return 0

    zf = zipfile.ZipFile(io.BytesIO(raw))
    n = 0
    buf: list[str] = []
    with zf.open(zf.namelist()[0]) as fh:
        for row in csv.reader(io.TextIOWrapper(fh, encoding="utf-8")):
            # aggId, price, qty, firstId, lastId, transactTime(us), isBuyerMaker, ...
            side = "SELL" if row[6].lower() == "true" else "BUY"
            ts_ns = int(row[5]) * 1000          # microseconds -> nanoseconds
            buf.append(
                f"{TABLE},symbol={SYMBOL},side={side} "
                f"price={float(row[1]):.8f},size={float(row[2]):.8f},agg_id={row[0]}i {ts_ns}"
            )
            if len(buf) >= BATCH:
                _post(buf)
                n += len(buf)
                buf = []
        if buf:
            _post(buf)
            n += len(buf)
    print(f"  {d}: ingested {n:,} rows")
    return n


def main(dates: list[date]) -> None:
    print(f"backfilling {len(dates)} day(s): {dates[0]} .. {dates[-1]} -> QuestDB '{TABLE}'")
    total = sum(ingest_day(d) for d in dates)
    print(f"done: {total:,} rows total")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--days":
        end = date.today() - timedelta(days=2)   # dumps lag ~1-2 days
        days = [end - timedelta(days=i) for i in range(int(args[1]) - 1, -1, -1)]
    else:
        days = [date.fromisoformat(a) for a in args]
    if not days:
        print("usage: python backfill.py YYYY-MM-DD [...]  |  --days N")
        sys.exit(1)
    main(days)
