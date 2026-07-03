"""Carry chapter backfill: Binance USDT-M futures dumps -> QuestDB.

Two tables feed the funding/basis study:
  funding           <- futures/um/monthly/fundingRate   (3 rows/day, every 8h)
  premium_index_1h  <- futures/um/monthly/premiumIndexKlines/1h  (the basis itself,
                       as a fraction: (perp - spot index) / spot index)

Format gotchas (VERIFIED on real files, not assumed):
  - These monthly futures CSVs HAVE a header row (spot daily aggTrades do not).
    Header presence varies across Binance datasets/years -> we SNIFF instead of
    assuming: a first cell that doesn't parse as int is a header.
  - fundingRate columns: calc_time, funding_interval_hours, last_funding_rate.
    calc_time is MILLISECONDS (aggTrades dumps were microseconds!) with small
    jitter (e.g. ...00005). Stored RAW - snapping to the 8h grid is analysis
    logic and lives in carry.py, the DB keeps what the exchange said.
  - premiumIndexKlines: standard 12-col kline CSV, open_time in ms; o/h/l/c are
    the premium as a fraction; volume columns are all zero (it's an index).

First explicit DDL in this repo: both tables are created PARTITION BY MONTH via
/exec before the first write. ILP auto-create would partition by DAY -> ~2,400
partitions of 3 rows each for funding. Month also makes the units coherent:
idempotency unit == partition unit == repair unit
  (ALTER TABLE funding DROP PARTITION LIST '2024-03';)

Usage:
  python backfill_futures.py 2020-01:2026-06     # inclusive month range
  python backfill_futures.py 2024-03 2024-04     # explicit months
  python backfill_futures.py --months 6          # last 6 COMPLETE months

Idempotent PER MONTH: a month that already has rows is skipped in each table
independently, so re-runs and overlapping ranges are safe.
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
from datetime import date

from backfill import _post
from config import QUESTDB_HTTP

SYMBOL = "BTCUSDT"
BASE = "https://data.binance.vision/data/futures/um/monthly"
BATCH = 10_000

# interval_hours is LONG (not INT) so the ILP `i` suffix maps without a cast.
FUNDING_DDL = """CREATE TABLE IF NOT EXISTS funding (
  symbol SYMBOL, rate DOUBLE, interval_hours LONG, timestamp TIMESTAMP
) TIMESTAMP(timestamp) PARTITION BY MONTH"""

PREMIUM_DDL = """CREATE TABLE IF NOT EXISTS premium_index_1h (
  symbol SYMBOL, o DOUBLE, h DOUBLE, l DOUBLE, c DOUBLE, timestamp TIMESTAMP
) TIMESTAMP(timestamp) PARTITION BY MONTH"""


def ensure_table(ddl: str) -> None:
    """Idempotent CREATE TABLE via /exec. A DB that is down = fail loud."""
    url = f"{QUESTDB_HTTP}/exec?query={urllib.parse.quote(ddl)}"
    try:
        urllib.request.urlopen(url, timeout=10)
    except urllib.error.URLError as e:
        raise SystemExit(f"QuestDB unreachable at {QUESTDB_HTTP}: {e}") from e


def month_row_count(table: str, ym: str) -> int:
    """Rows already stored for month ym ('YYYY-MM'). 0 also when the table does
    not exist yet; a QuestDB that is DOWN fails loudly instead of skipping."""
    q = f"select count() from {table} where timestamp in '{ym}'"
    url = f"{QUESTDB_HTTP}/exec?query={urllib.parse.quote(q)}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return int(json.loads(resp.read())["dataset"][0][0])
    except urllib.error.HTTPError:
        return 0  # 400 = table does not exist yet
    except urllib.error.URLError as e:
        raise SystemExit(f"QuestDB unreachable at {QUESTDB_HTTP}: {e}") from e


def is_header(row: list[str]) -> bool:
    """Sniff a header row: data rows start with an epoch-ms integer."""
    try:
        int(row[0])
        return False
    except ValueError:
        return True


def funding_row_to_line(row: list[str]) -> str:
    """calc_time(ms), funding_interval_hours, last_funding_rate -> ILP line."""
    ts_ns = int(row[0]) * 1_000_000          # ms -> ns (aggTrades dumps were us!)
    return (
        f"funding,symbol={SYMBOL} "
        f"rate={float(row[2]):.8f},interval_hours={int(row[1])}i {ts_ns}"
    )


def premium_row_to_line(row: list[str]) -> str:
    """12-col kline row -> ILP line; open_time(ms) is the bar timestamp."""
    ts_ns = int(row[0]) * 1_000_000
    return (
        f"premium_index_1h,symbol={SYMBOL} "
        f"o={float(row[1]):.8f},h={float(row[2]):.8f},"
        f"l={float(row[3]):.8f},c={float(row[4]):.8f} {ts_ns}"
    )


def ingest_month(table: str, url_prefix: str, ym: str, to_line) -> int:
    """url_prefix + '-YYYY-MM.zip' is the dump; one month = one idempotency unit."""
    have = month_row_count(table, ym)
    if have > 0:
        print(f"  {ym} {table}: already have {have:,} rows — skip")
        return 0

    url = f"{url_prefix}-{ym}.zip"
    print(f"  {ym} {table}: downloading ...", flush=True)
    try:
        raw = urllib.request.urlopen(url, timeout=180).read()
    except Exception as e:  # noqa: BLE001
        print(f"  {ym} {table}: !! download failed ({e}) — skipping")
        return 0

    zf = zipfile.ZipFile(io.BytesIO(raw))
    n = 0
    buf: list[str] = []
    with zf.open(zf.namelist()[0]) as fh:
        for row in csv.reader(io.TextIOWrapper(fh, encoding="utf-8")):
            if is_header(row):
                continue
            buf.append(to_line(row))
            if len(buf) >= BATCH:
                _post(buf)
                n += len(buf)
                buf = []
        if buf:
            _post(buf)
            n += len(buf)
    print(f"  {ym} {table}: ingested {n:,} rows")
    return n


def expand_arg(a: str) -> list[str]:
    """'2024-03' -> [itself]; '2020-01:2026-06' -> inclusive month range."""
    if ":" not in a:
        return [a]
    lo, hi = a.split(":")
    y, m = (int(x) for x in lo.split("-"))
    y1, m1 = (int(x) for x in hi.split("-"))
    out: list[str] = []
    while (y, m) <= (y1, m1):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def main(months: list[str]) -> None:
    ensure_table(FUNDING_DDL)
    ensure_table(PREMIUM_DDL)
    print(f"backfilling {len(months)} month(s): {months[0]} .. {months[-1]}")
    total = 0
    for ym in months:
        total += ingest_month(
            "funding", f"{BASE}/fundingRate/{SYMBOL}/{SYMBOL}-fundingRate", ym,
            funding_row_to_line)
        total += ingest_month(
            "premium_index_1h", f"{BASE}/premiumIndexKlines/{SYMBOL}/1h/{SYMBOL}-1h", ym,
            premium_row_to_line)
    print(f"done: {total:,} rows total")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--months":
        today = date.today()
        y, m = today.year, today.month     # current month is incomplete -> start at previous
        months: list[str] = []
        for _ in range(int(args[1])):
            m -= 1
            if m == 0:
                y, m = y - 1, 12
            months.append(f"{y:04d}-{m:02d}")
        months.reverse()
    else:
        months = [ym for a in args for ym in expand_arg(a)]
    if not months:
        print("usage: python backfill_futures.py YYYY-MM[:YYYY-MM] [...]  |  --months N")
        sys.exit(1)
    main(months)
