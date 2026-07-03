"""Futures-dump parsers: exact ILP lines, the header sniff (presence varies
across Binance datasets — we detect, not assume), and month-range expansion.
"""
from __future__ import annotations

from backfill_futures import (
    expand_arg,
    funding_row_to_line,
    is_header,
    premium_row_to_line,
)


def test_funding_row_exact_line_ms_to_ns():
    row = ["1585699200005", "8", "-0.00003746"]
    assert funding_row_to_line(row) == (
        "funding,symbol=BTCUSDT rate=-0.00003746,interval_hours=8i 1585699200005000000"
    )


def test_premium_row_exact_line_uses_open_time_and_ohlc():
    row = ["1777593600000", "-0.00048320", "-0.00029579", "-0.00091522", "-0.00054762",
           "0", "1777597199999", "0", "720", "0", "0", "0"]
    assert premium_row_to_line(row) == (
        "premium_index_1h,symbol=BTCUSDT "
        "o=-0.00048320,h=-0.00029579,l=-0.00091522,c=-0.00054762 1777593600000000000"
    )


def test_header_sniff_both_ways():
    assert is_header(["calc_time", "funding_interval_hours", "last_funding_rate"])
    assert not is_header(["1585699200005", "8", "-0.00003746"])   # headerless file: row 1 is data


def test_expand_arg_single_and_inclusive_range_with_rollover():
    assert expand_arg("2024-03") == ["2024-03"]
    assert expand_arg("2023-11:2024-02") == ["2023-11", "2023-12", "2024-01", "2024-02"]
    assert expand_arg("2024-05:2024-05") == ["2024-05"]           # endpoints inclusive
