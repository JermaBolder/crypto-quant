"""Ethena eval pure logic: 8h->daily aggregation (incident days dropped AND
counted) and the day-floored panel join with the multiplier/prediction math.
"""
from __future__ import annotations

import pandas as pd
import pytest

from ethena_eval import build_panel, daily_funding


def test_daily_funding_sums_full_days_and_counts_incidents():
    ts = pd.Series(pd.to_datetime([
        "2024-03-01 00:00:00.005", "2024-03-01 08:00:00", "2024-03-01 16:00:00.001",
        "2024-03-02 00:00:00", "2024-03-02 08:00:00",   # incident: 2 settlements
    ], format="ISO8601"))
    rate = pd.Series([0.0001, 0.0002, -0.0001, 0.0005, 0.0005])
    daily, dropped = daily_funding(ts, rate)
    assert dropped == 1
    assert list(daily.index) == [pd.Timestamp("2024-03-01")]
    assert daily.iloc[0] == pytest.approx(0.0002)


def test_build_panel_floors_days_inner_joins_and_multiplies():
    susde = pd.DataFrame({
        # snapshots carry the ~23:01Z jitter -> must floor to their own day
        "timestamp": pd.Series(pd.to_datetime(
            ["2024-03-01 23:01:19", "2024-03-02 23:01:02"])),
        "apy": [10.0, 12.0],
        "tvl_usd": [2e9, 2e9],
    })
    supply = pd.DataFrame({
        "timestamp": pd.Series(pd.to_datetime(
            ["2024-03-01", "2024-03-02", "2024-03-03"])),
        "circulating": [4e9, 5e9, 6e9],
    })
    fund = {"btc": pd.Series([0.0003], index=pd.to_datetime(["2024-03-01"]))}
    df = build_panel(susde, supply, fund)
    # inner join keeps only the day every input observed
    assert list(df.index) == [pd.Timestamp("2024-03-01")]
    assert df["mult"].iloc[0] == pytest.approx(2.0)          # 4e9 circulating / 2e9 staked
    assert df["pred_btc"].iloc[0] == pytest.approx(0.0003 * 2.0 * 365 * 100)
