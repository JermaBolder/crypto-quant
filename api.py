"""Phase 3: the API layer between QuestDB and the dashboard.

WHY an API at all (instead of the browser querying QuestDB directly):
the DB endpoint accepts ARBITRARY SQL — exposing it to a browser page means
anyone who can open the page can drop tables. The API is the boundary: it
exposes three narrow, read-only questions the UI is allowed to ask. That
boundary is the lesson of this phase, not ceremony.

Endpoints (all JSON):
  GET /health          — is the pipeline alive? (latest trade age = the honest
                         health metric: containers can be "up" while data stalls)
  GET /bars?minutes=N  — 1m OHLC + order-flow delta bars from the LIVE table
  GET /stats           — last price, 1h volume/delta/buy-share, trades/min

Run (host venv, next to the containers):
  .venv/bin/uvicorn api:app --port 8000 --reload

Endpoints are sync `def` on purpose: FastAPI runs them in a threadpool, and
with one consumer (the dashboard) on localhost, async buys nothing here and
would only add concepts. Queries go to QuestDB /exec via stdlib urllib —
same zero-dependency pattern as the rest of the project.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from config import QUESTDB_HTTP

app = FastAPI(title="crypto-quant api")

# CORS: browsers block JS on origin A (the dashboard, :3000) from calling
# origin B (this API, :8000) unless B explicitly allows it. Allow ONLY the
# local dashboard origin — not "*".
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def q(sql: str) -> list[list]:
    """Run one read-only query against QuestDB, return dataset rows."""
    url = f"{QUESTDB_HTTP}/exec?" + urllib.parse.urlencode({"query": sql})
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read())["dataset"]
    except urllib.error.URLError as e:
        raise HTTPException(status_code=503, detail=f"questdb unreachable: {e}") from e


@app.get("/health")
def health() -> dict:
    rows = q("SELECT timestamp FROM trades LIMIT -1")
    if not rows:
        return {"ok": False, "latest_trade": None, "age_s": None}
    latest = rows[0][0]
    age = (datetime.now(timezone.utc)
           - datetime.fromisoformat(latest.replace("Z", "+00:00"))).total_seconds()
    # >60s of silence on BTCUSDT (which prints many times a second) = stalled
    return {"ok": age < 60, "latest_trade": latest, "age_s": round(age, 1)}


@app.get("/bars")
def bars(minutes: int = Query(default=180, ge=1, le=1440)) -> list[dict]:
    """1m bars over the last `minutes` (capped at a day — this is a live view,
    history research lives in dataset.py, not in the dashboard)."""
    rows = q(f"""
        SELECT timestamp,
               first(price) o, max(price) h, min(price) l, last(price) c,
               sum(size) vol,
               sum(size * CASE WHEN side='BUY' THEN 1 ELSE -1 END) delta
        FROM trades
        WHERE timestamp > dateadd('m', -{minutes}, now())
        SAMPLE BY 1m
    """)
    return [
        {"t": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4],
         "vol": round(r[5], 4), "delta": round(r[6], 4)}
        for r in rows
    ]


@app.get("/stats")
def stats() -> dict:
    last = q("SELECT price FROM trades LIMIT -1")
    hour = q("""
        SELECT sum(size),
               sum(CASE WHEN side='BUY' THEN size ELSE 0 END),
               sum(size * CASE WHEN side='BUY' THEN 1 ELSE -1 END)
        FROM trades WHERE timestamp > dateadd('h', -1, now())
    """)
    rate = q("SELECT count() FROM trades WHERE timestamp > dateadd('m', -5, now())")
    vol, buy_vol, delta = (hour[0] if hour else (None, None, None))
    return {
        "last_price": last[0][0] if last else None,
        "vol_1h": round(vol, 4) if vol else 0.0,
        "buy_share_1h": round(buy_vol / vol, 4) if vol else None,
        "delta_1h": round(delta, 4) if delta is not None else 0.0,
        "trades_per_min": round((rate[0][0] if rate else 0) / 5.0, 1),
    }
