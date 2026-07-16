"""Ethena backfill parsers: ISO-with-jitter -> ns, epoch-day -> ns, exact ILP
lines, and the complete-days-only window (today's live snapshot must never be
stored) for both the yield and the supply feeds.
"""
from __future__ import annotations

from backfill_ethena import (
    day_point_ns,
    iso_to_ns,
    point_to_line,
    select_new,
    supply_to_line,
)

POINT = {"timestamp": "2024-02-16T23:01:19.228Z", "tvlUsd": 141971012,
         "apy": 24.705146546545034, "apyBase": 24.705146546545034}


def test_iso_to_ns_keeps_ms_precision():
    assert iso_to_ns("2024-02-16T23:01:19.228Z") == 1_708_124_479_228_000_000
    assert iso_to_ns("2026-07-09T00:00:00.000Z") == 1_783_555_200_000_000_000


def test_point_to_line_exact_ilp():
    assert point_to_line(POINT) == (
        "susde_yield,pool=ethena-susde "
        "apy=24.705147,tvl_usd=141971012.00 1708124479228000000"
    )


def test_select_new_is_strictly_between_since_and_cutoff():
    days = [{"timestamp": f"2026-07-0{d}T23:01:00.000Z", "tvlUsd": 1.0, "apy": 1.0}
            for d in range(1, 8)]
    since = iso_to_ns("2026-07-03T23:01:00.000Z")       # already stored through the 3rd
    cutoff = iso_to_ns("2026-07-07T00:00:00.000Z")      # today: the 7th is partial
    picked = [p["timestamp"][:10] for p in select_new(days, since, cutoff)]
    assert picked == ["2026-07-04", "2026-07-05", "2026-07-06"]


def test_select_new_empty_table_takes_all_complete_days():
    days = [{"timestamp": "2026-07-05T23:01:00.000Z"},
            {"timestamp": "2026-07-06T14:01:00.000Z"}]  # partial today
    picked = select_new(days, 0, iso_to_ns("2026-07-06T00:00:00.000Z"))
    assert [p["timestamp"][:10] for p in picked] == ["2026-07-05"]


# --- USDe supply feed (epoch-second days, live point stamped AT the cutoff) ---

SUPPLY_POINT = {"date": 1702252800, "circulating": {"peggedUSD": 4945329.0}}


def test_supply_to_line_exact_ilp():
    assert supply_to_line(SUPPLY_POINT) == (
        "usde_supply,asset=usde circulating=4945329.00 1702252800000000000"
    )


def test_select_new_supply_excludes_live_point_at_cutoff():
    # 2026-07-13, -14, -15 midnights; "today" = the 15th -> its point is the
    # live in-place-updated snapshot and sits EXACTLY at the cutoff: excluded
    # by strictly-less, no special case needed.
    days = [{"date": 1784073600 - 2 * 86400}, {"date": 1784073600 - 86400},
            {"date": 1784073600}]
    picked = select_new(days, 0, 1784073600 * 1_000_000_000, ts_of=day_point_ns)
    assert [p["date"] for p in picked] == [1784073600 - 2 * 86400, 1784073600 - 86400]
    # incremental: already stored through the 13th -> only the 14th is new
    picked = select_new(days, (1784073600 - 2 * 86400) * 1_000_000_000,
                        1784073600 * 1_000_000_000, ts_of=day_point_ns)
    assert [p["date"] for p in picked] == [1784073600 - 86400]
