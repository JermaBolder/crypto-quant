"""API handlers with the single DB seam (api.q) stubbed — no QuestDB needed.

The transforms ARE the API contract the dashboard renders: age->ok in /health,
row->dict mapping in /bars, ratio math and null handling in /stats.
"""
from __future__ import annotations

import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

import api

client = TestClient(api.app)


def iso_z(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


# --- /health ---

def test_health_fresh_trade_is_ok(monkeypatch):
    monkeypatch.setattr(api, "q", lambda sql: [[iso_z(datetime.now(UTC) - timedelta(seconds=5))]])
    body = client.get("/health").json()
    assert body["ok"] is True
    assert 3 <= body["age_s"] <= 8


def test_health_stale_trade_is_not_ok(monkeypatch):
    monkeypatch.setattr(api, "q", lambda sql: [[iso_z(datetime.now(UTC) - timedelta(seconds=120))]])
    body = client.get("/health").json()
    assert body["ok"] is False
    assert body["age_s"] > 60


def test_health_empty_table(monkeypatch):
    monkeypatch.setattr(api, "q", lambda sql: [])
    assert client.get("/health").json() == {"ok": False, "latest_trade": None, "age_s": None}


def test_health_questdb_down_is_503(monkeypatch):
    def boom(*a, **kw):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(urllib.request, "urlopen", boom)   # exercises the REAL q()
    resp = client.get("/health")
    assert resp.status_code == 503
    assert "questdb unreachable" in resp.json()["detail"]


# --- /bars ---

def test_bars_row_mapping_and_rounding(monkeypatch):
    row = ["2026-07-03T00:00:00.000000Z", 1.0, 2.0, 0.5, 1.5, 10.123456, -3.987654]
    monkeypatch.setattr(api, "q", lambda sql: [row])
    assert client.get("/bars").json() == [{
        "t": "2026-07-03T00:00:00.000000Z",
        "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5,
        "vol": 10.1235, "delta": -3.9877,
    }]


def test_bars_minutes_bounds_are_enforced(monkeypatch):
    monkeypatch.setattr(api, "q", lambda sql: [])
    assert client.get("/bars", params={"minutes": 0}).status_code == 422
    assert client.get("/bars", params={"minutes": 2000}).status_code == 422
    assert client.get("/bars", params={"minutes": 1440}).status_code == 200


# --- /stats ---

def fake_q_stats(sql: str) -> list[list]:
    if "count()" in sql:
        return [[600]]                            # 600 trades in 5m
    if "sum(size)" in sql:
        return [[10.0, 6.0, 2.0]]                 # vol, buy_vol, delta over 1h
    return [[61000.5]]                            # last price


def test_stats_ratio_math(monkeypatch):
    monkeypatch.setattr(api, "q", fake_q_stats)
    assert client.get("/stats").json() == {
        "last_price": 61000.5,
        "vol_1h": 10.0,
        "buy_share_1h": 0.6,
        "delta_1h": 2.0,
        "trades_per_min": 120.0,
    }


def test_stats_handles_empty_hour(monkeypatch):
    def empty_q(sql: str) -> list[list]:
        if "count()" in sql:
            return [[0]]
        if "sum(size)" in sql:
            return [[None, None, None]]           # QuestDB: aggregates over no rows
        return []                                 # no trades at all
    monkeypatch.setattr(api, "q", empty_q)
    assert client.get("/stats").json() == {
        "last_price": None,
        "vol_1h": 0.0,
        "buy_share_1h": None,
        "delta_1h": 0.0,
        "trades_per_min": 0.0,
    }


# --- /funding ---

def fake_q_funding(sql: str) -> list[list]:
    if "FROM funding" in sql:
        return [
            ["2026-06-30T00:00:00.000000Z", 0.0001],
            ["2026-06-30T08:00:00.000000Z", -0.0002],
            ["2026-06-30T16:00:00.000000Z", 0.0003],
            ["2026-07-01T00:00:00.000000Z", 0.0004],
        ]
    return [[0.0005]]                                  # premium_index_1h latest o (basis)


def test_funding_stats_and_series(monkeypatch):
    monkeypatch.setattr(api, "q", fake_q_funding)
    body = client.get("/funding").json()
    # mean rate = (1 - 2 + 3 + 4)/4 bps = 1.5 bps; annualized = 0.00015*1095*100
    # = 16.425, which rounds to 16.42 (float repr of x.425 lands just below)
    assert body == {
        "latest_rate_bps": 4.0,
        "latest_ts": "2026-07-01T00:00:00.000000Z",
        "mean_rate_bps": 1.5,
        "pct_positive": 0.75,
        "annualized_pct": 16.42,
        "latest_basis_bps": 5.0,
        "series": [
            {"t": "2026-06-30T00:00:00.000000Z", "rate_bps": 1.0},
            {"t": "2026-06-30T08:00:00.000000Z", "rate_bps": -2.0},
            {"t": "2026-06-30T16:00:00.000000Z", "rate_bps": 3.0},
            {"t": "2026-07-01T00:00:00.000000Z", "rate_bps": 4.0},
        ],
    }


def test_funding_intervals_bounds(monkeypatch):
    monkeypatch.setattr(api, "q", fake_q_funding)
    assert client.get("/funding", params={"intervals": 0}).status_code == 422
    assert client.get("/funding", params={"intervals": 1001}).status_code == 422
    assert client.get("/funding", params={"intervals": 1000}).status_code == 200


def test_funding_empty_table(monkeypatch):
    monkeypatch.setattr(api, "q", lambda sql: [])
    assert client.get("/funding").json() == {
        "latest_rate_bps": None, "latest_ts": None, "mean_rate_bps": None,
        "pct_positive": None, "annualized_pct": None,
        "latest_basis_bps": None, "series": [],
    }
