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

Idempotent PER DAY: a day that already has rows is skipped, so re-runs and
overlapping ranges are safe. The unit of repair is the day: if a day ever gets
half-ingested (crash mid-run), drop just that day's partition and re-run:
  ALTER TABLE agg_trades DROP PARTITION LIST '2026-06-24';
(QuestDB has no row-level DELETE; day partitions are the deletion unit.)
"""
from __future__ import annotations

import csv
import io
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import date, timedelta

from config import QUESTDB_HTTP

SYMBOL = "BTCUSDT"
TABLE = "agg_trades"
QUESTDB_WRITE = f"{QUESTDB_HTTP}/write"
BASE = "https://data.binance.vision/data/spot/daily/aggTrades"
BATCH = 10_000


def _post(lines: list[str]) -> None:
    body = ("\n".join(lines) + "\n").encode()
    req = urllib.request.Request(QUESTDB_WRITE, data=body, method="POST")
    urllib.request.urlopen(req, timeout=30)  # raises on non-2xx


def day_row_count(d: date) -> int:
    """Rows already stored for day d. 0 also when the table doesn't exist yet
    (first ever run — ILP auto-creates it on write). A QuestDB that is DOWN is
    a different story: fail loudly instead of 'skipping nothing' silently.
    """
    # WHERE timestamp IN '2026-06-24' is QuestDB's whole-day interval syntax.
    q = f"select count() from {TABLE} where timestamp in '{d.isoformat()}'"
    url = f"{QUESTDB_HTTP}/exec?query={urllib.parse.quote(q)}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return int(json.loads(resp.read())["dataset"][0][0])
    except urllib.error.HTTPError:
        return 0  # 400 = table does not exist yet
    except urllib.error.URLError as e:
        raise SystemExit(f"QuestDB unreachable at {QUESTDB_HTTP}: {e}") from e


def row_to_line(row: list[str]) -> str:
    """One dump CSV row -> ILP line. transactTime is MICROseconds -> *1000 = ns."""
    # aggId, price, qty, firstId, lastId, transactTime(us), isBuyerMaker, ...
    side = "SELL" if row[6].lower() == "true" else "BUY"
    ts_ns = int(row[5]) * 1000
    return (
        f"{TABLE},symbol={SYMBOL},side={side} "
        f"price={float(row[1]):.8f},size={float(row[2]):.8f},agg_id={row[0]}i {ts_ns}"
    )


def ingest_day(d: date) -> int:
    have = day_row_count(d)
    if have > 0:
        print(f"  {d}: already have {have:,} rows — skip")
        return 0

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
            buf.append(row_to_line(row))
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
