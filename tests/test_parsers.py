"""The two ingest parsers and THE timestamp gotcha: the live websocket gives
milliseconds, the historical dumps give MICROseconds. Both must land on the
same nanosecond timeline or live and backfilled data silently disagree.
"""
from __future__ import annotations

from backfill import row_to_line
from sources import parse_trade

WS_MSG = {
    "e": "trade", "E": 1_700_000_000_125, "s": "BTCUSDT", "t": 987654321,
    "p": "50000.10", "q": "0.00420000", "T": 1_700_000_000_123, "m": False, "M": True,
}


def test_parse_trade_fields_and_ms_to_ns():
    t = parse_trade(WS_MSG)
    assert t.ts_ns == 1_700_000_000_123_000_000       # ms * 1_000_000
    assert t.symbol == "BTCUSDT"
    assert t.price == 50000.10 and isinstance(t.price, float)
    assert t.size == 0.0042 and isinstance(t.size, float)
    assert t.trade_id == 987654321 and isinstance(t.trade_id, int)


def test_parse_trade_aggressor_side():
    # m = buyer-is-maker: True means the SELLER crossed the spread
    assert parse_trade({**WS_MSG, "m": False}).side == "BUY"
    assert parse_trade({**WS_MSG, "m": True}).side == "SELL"


def test_row_to_line_exact_line_and_us_to_ns():
    # aggId, price, qty, firstId, lastId, transactTime(us), isBuyerMaker, isBestMatch
    row = ["42", "50000.10", "0.0042", "1", "5", "1700000000123456", "True", "True"]
    assert row_to_line(row) == (
        "agg_trades,symbol=BTCUSDT,side=SELL "
        "price=50000.10000000,size=0.00420000,agg_id=42i 1700000000123456000"
    )


def test_row_to_line_side_is_case_insensitive():
    row = ["1", "1.0", "1.0", "1", "1", "1000000", "false", "True"]
    assert " side=BUY " not in row_to_line(row)       # side is a tag, not a field
    assert ",side=BUY " in row_to_line(row)
    assert ",side=SELL " in row_to_line(["1", "1.0", "1.0", "1", "1", "1000000", "true", "T"])
    assert ",side=SELL " in row_to_line(["1", "1.0", "1.0", "1", "1", "1000000", "TRUE", "T"])


def test_ws_and_dump_agree_on_the_same_instant():
    # one instant, two encodings: ms on the websocket, us in the dump
    instant_ms = 1_700_000_000_123
    ws_ns = parse_trade({**WS_MSG, "T": instant_ms}).ts_ns
    row = ["1", "1.0", "1.0", "1", "1", str(instant_ms * 1000), "false", "T"]
    dump_ns = int(row_to_line(row).rsplit(" ", 1)[1])
    assert ws_ns == dump_ns == 1_700_000_000_123_000_000
