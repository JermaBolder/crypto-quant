"""Ops watchdog: pipeline freshness -> Telegram alerts on state changes.

WHY freshness and not container status: every container can be "Up" while data
silently stalls (WS drop, consumer wedge, full disk). The latest trade age in
QuestDB is the end-to-end signal — it covers producer, Redis, consumer and the
DB in one number. Same metric as /health in api.py, but pushed to a human
instead of waiting for one to look.

State machine with hysteresis so it never flaps:
  OK    -> STALE  when age >= ALERT_AFTER_S (or the table is empty)
  STALE -> OK     only when age < RECOVER_BELOW_S
  DOWN            when QuestDB itself is unreachable
In the band between the two thresholds the previous side wins. Alerts fire
ONLY on transitions; a broken pipeline at boot alerts on the first poll
(initial state is OK), a healthy boot stays silent.

Telegram: POST to the Bot API with TG_BOT_TOKEN/TG_CHAT_ID from env. Empty =
push disabled, the watchdog still logs transitions (log-only mode).

Usage:  python watchdog.py            # daemon (compose service cq_watchdog)
        python watchdog.py 70         # timed test run, then exit
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime

from config import QUESTDB_HTTP, TG_BOT_TOKEN, TG_CHAT_ID

POLL_S = 30
ALERT_AFTER_S = 120
RECOVER_BELOW_S = 60


def probe() -> tuple[str, float | None]:
    """One freshness check: ("down"|"empty"|"data", age_s)."""
    sql = "SELECT timestamp FROM trades LIMIT -1"
    url = f"{QUESTDB_HTTP}/exec?" + urllib.parse.urlencode({"query": sql})
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            rows = json.loads(resp.read())["dataset"]
    except urllib.error.URLError:
        return "down", None
    if not rows:
        return "empty", None
    latest = datetime.fromisoformat(rows[0][0].replace("Z", "+00:00"))
    return "data", (datetime.now(UTC) - latest).total_seconds()


def classify(prev: str, kind: str, age: float | None) -> str:
    """Hysteresis: alert at >= ALERT_AFTER_S, recover only below RECOVER_BELOW_S."""
    if kind == "down":
        return "DOWN"
    if kind == "empty":
        return "STALE"
    assert age is not None
    if age >= ALERT_AFTER_S:
        return "STALE"
    if age < RECOVER_BELOW_S:
        return "OK"
    return "STALE" if prev in ("STALE", "DOWN") else "OK"   # the in-between band


def alert_text(state: str, age: float | None) -> str:
    if state == "DOWN":
        return "🔴 crypto-quant: QuestDB unreachable"
    if state == "STALE":
        detail = f"last trade {age:.0f}s ago" if age is not None else "no data in trades"
        return f"⚠️ crypto-quant: data stalled — {detail}"
    return f"✅ crypto-quant: recovered — last trade {age:.1f}s ago"


def notify(text: str) -> None:
    """Telegram push; no-op without credentials (log-only mode)."""
    if not (TG_BOT_TOKEN and TG_CHAT_ID):
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    body = urllib.parse.urlencode({"chat_id": TG_CHAT_ID, "text": text}).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    urllib.request.urlopen(req, timeout=10)


def step(prev: str) -> tuple[str, str | None]:
    """One poll: new state + alert text if (and only if) the state changed."""
    kind, age = probe()
    state = classify(prev, kind, age)
    return state, alert_text(state, age) if state != prev else None


def run(max_seconds: float | None = None, poll_s: float = POLL_S) -> None:
    mode = "push" if (TG_BOT_TOKEN and TG_CHAT_ID) else "LOG-ONLY (no TG_* env)"
    print(f"watchdog: trades freshness every {poll_s:.0f}s, alerts: {mode} [daemon]")
    start = time.monotonic()
    state = "OK"
    while True:
        state, alert = step(state)
        if alert:
            print(f"[watchdog] {alert}")
            try:
                notify(alert)
            except Exception as e:  # noqa: BLE001
                print(f"[watchdog] notify failed: {e}")   # alerting must not kill the loop
        if max_seconds is not None and time.monotonic() - start >= max_seconds:
            print(f"[watchdog] timed run done, state={state}")
            break
        time.sleep(poll_s)


if __name__ == "__main__":
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else None
    try:
        run(secs, poll_s=min(POLL_S, secs) if secs else POLL_S)
    except KeyboardInterrupt:
        print("\nstopped.")
