"""Pin the ILP wire format: QuestDB parses these bytes, so the exact string
(tags vs fields, the `i` integer suffix, no scientific notation) IS the contract.
"""
from __future__ import annotations

from qdb_sink import line


def test_line_exact_wire_format():
    out = line("BTCUSDT", "BUY", 50000.0, 0.001, 123, 1_700_000_000_000_000_000)
    assert out == (
        "trades,symbol=BTCUSDT,side=BUY "
        "price=50000.00000000,size=0.00100000,id=123i 1700000000000000000"
    )


def test_line_tiny_floats_never_scientific_notation():
    # f"{1e-07}" would give "1e-07" — ILP rejects it; :.8f is the guarantee
    out = line("BTCUSDT", "SELL", 1e-07, 2e-08, 1, 1)
    assert "e-" not in out and "E-" not in out
    assert "price=0.00000010" in out
    assert "size=0.00000002" in out


def test_line_id_is_ilp_integer_and_ts_is_last():
    out = line("ETHUSDT", "SELL", 3000.5, 1.5, 42, 1_700_000_000_000_000_001)
    fields = out.split(" ")[1]
    assert fields.endswith("id=42i")            # `i` suffix = ILP long, not double
    assert out.rsplit(" ", 1)[1] == "1700000000000000001"
