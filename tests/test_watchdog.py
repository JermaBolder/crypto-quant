"""Watchdog state machine + alert plumbing: the hysteresis band never flaps,
alerts fire exactly on transitions, and the Telegram POST is a no-op without
credentials. All pure — probe and urllib are stubbed.
"""
from __future__ import annotations

import urllib.parse
import urllib.request

import watchdog
from watchdog import ALERT_AFTER_S, RECOVER_BELOW_S, classify, step

# --- classify: the hysteresis state machine ---

def test_fresh_data_is_ok_from_any_state():
    for prev in ("OK", "STALE", "DOWN"):
        assert classify(prev, "data", 5.0) == "OK"


def test_stale_age_alerts_from_ok():
    assert classify("OK", "data", ALERT_AFTER_S) == "STALE"       # >= threshold
    assert classify("OK", "data", ALERT_AFTER_S - 1) == "OK"      # just under


def test_band_keeps_previous_side_no_flapping():
    mid = (ALERT_AFTER_S + RECOVER_BELOW_S) / 2
    assert classify("OK", "data", mid) == "OK"          # healthy stays healthy
    assert classify("STALE", "data", mid) == "STALE"    # broken stays broken
    assert classify("DOWN", "data", mid) == "STALE"     # DB is back but data not fresh yet


def test_down_and_empty():
    assert classify("OK", "down", None) == "DOWN"
    assert classify("OK", "empty", None) == "STALE"


# --- step: alerts only on transitions ---

def test_step_alerts_once_then_stays_silent(monkeypatch):
    monkeypatch.setattr(watchdog, "probe", lambda: ("data", 300.0))
    state, alert = step("OK")
    assert state == "STALE" and "data stalled" in alert and "300s ago" in alert
    state, alert = step(state)
    assert state == "STALE" and alert is None                     # no repeat spam


def test_step_recovery_alert(monkeypatch):
    monkeypatch.setattr(watchdog, "probe", lambda: ("data", 2.0))
    state, alert = step("STALE")
    assert state == "OK" and "recovered" in alert and "2.0s ago" in alert


def test_step_down_alert(monkeypatch):
    monkeypatch.setattr(watchdog, "probe", lambda: ("down", None))
    state, alert = step("OK")
    assert state == "DOWN" and "QuestDB unreachable" in alert


# --- notify: Telegram POST plumbing ---

def test_notify_posts_token_url_and_chat_id(monkeypatch):
    seen = {}

    def spy(req, timeout=None):
        seen["url"] = req.full_url
        seen["body"] = req.data.decode()
        raise SystemExit  # never reach the network

    monkeypatch.setattr(watchdog, "TG_BOT_TOKEN", "123:abc")
    monkeypatch.setattr(watchdog, "TG_CHAT_ID", "42")
    monkeypatch.setattr(urllib.request, "urlopen", spy)
    try:
        watchdog.notify("boom")
    except SystemExit:
        pass
    assert seen["url"] == "https://api.telegram.org/bot123:abc/sendMessage"
    params = dict(urllib.parse.parse_qsl(seen["body"]))
    assert params == {"chat_id": "42", "text": "boom"}


def test_notify_is_noop_without_credentials(monkeypatch):
    def explode(*a, **kw):
        raise AssertionError("must not touch the network in log-only mode")

    monkeypatch.setattr(watchdog, "TG_BOT_TOKEN", "")
    monkeypatch.setattr(urllib.request, "urlopen", explode)
    watchdog.notify("boom")                                       # silently does nothing
