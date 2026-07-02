# crypto-quant — modern-stack learning build

Goal: learn the modern data/quant stack by building a real one. Money was
explicitly secondary; the honest research verdict below is part of the point.

Roadmap: **0 data+DB ✓ · 1 streaming ✓ · 2 ML signals ✓ · 5 prod ✓ · ML v2 ✓ (closed: no edge)**

## Architecture
```
Binance WS ──producer──▶ Redis Stream "trades" ──consumer(group)──▶ QuestDB
   (cq_producer)             (cq_redis)            (cq_consumer)    (cq_questdb)
```
All four run as Docker containers (`restart: unless-stopped`, healthchecks,
data in the `qdb_data` volume). Producer and consumer are decoupled in time:
either can crash, restart, or lag without taking the other down. The consumer
ACKs an entry only *after* the DB write succeeds (at-least-once delivery).

Research path (offline, on the host):
```
data.binance.vision ──backfill──▶ agg_trades ──dataset──▶ features+label ──model──▶ verdict
```

## Research verdict (v1 + v2): NO EDGE — and that's the result
- **v1** (14 days, 9 features, logreg): OOS hit 48.3%, net −16.3 bps/bet.
- **v2** (90 days / 80.3M trades, 22 features incl. trade-size structure &
  vol-regime, vol-scaled label dead zone, purged walk-forward, logreg +
  gradient boosting, abstain threshold picked inside train):
  every config negative, 0/5 positive folds everywhere. Best: HGB @ H=60m,
  hit 51.8% but net **−13.1 bps/bet** — the model does find a weak
  statistical signal (~+2 bps gross vs ~15 bps round-trip cost), i.e.
  *predictability without tradability*.
- Stop rule agreed in advance: net ≤ 0 ⇒ iteration closed, no further tuning.
  Weak public-data signals on 1m BTC do not survive costs. Verified, twice.

## Files
| file | what |
|---|---|
| `sources.py` | `Trade` + pluggable `TradeSource` (Binance now; anything later). |
| `producer.py` | WS trades → Redis Stream (`XADD`, capped ~100k entries). |
| `consumer_questdb.py` | Redis Stream → QuestDB via consumer group `cg_questdb`. |
| `consumer_metrics.py` | 2nd consumer, rolling 60s order-flow delta (fan-out demo). |
| `qdb_sink.py` | QuestDB writer (line protocol over HTTP, stdlib only). |
| `config.py` | env-based config (host vs containers), 12-factor style. |
| `backfill.py` | daily aggTrades dumps → `agg_trades`; idempotent per day. |
| `dataset.py` | 1m order-flow bars → 22 features + vol-scaled dead-zone label. |
| `evaluate.py` | baselines-in-money harness + purged walk-forward splits. |
| `model.py` | logreg + HistGradientBoosting, abstain-τ inside train, stop-rule verdict. |
| `docker-compose.yml`, `Dockerfile` | the whole pipeline as supervised containers. |
| `legacy/ingest.py`, `run_questdb.sh`, `runtime/` | retired pre-Docker path (kept for history). |

## Run
```bash
# Docker runtime (colima autostarts at login via brew services)
colima start                    # only needed manually if the service is off
docker compose up -d            # builds cq_app, starts all four containers
docker compose logs -f producer consumer

# inspect the data
curl -sG http://localhost:9000/exec --data-urlencode \
  "query=SELECT side, count(), sum(size) FROM trades"

# research (host venv: pandas/sklearn stay OUT of the runtime image)
.venv/bin/python backfill.py --days 90   # idempotent: re-runs skip loaded days
.venv/bin/python dataset.py              # class-balance sweep across horizons
.venv/bin/python evaluate.py             # baselines (the bar to clear)
.venv/bin/python model.py                # models + verdict
```

## Stack notes (macOS arm64)
- Docker via colima (no Docker Desktop); `brew services start colima` = autostart.
- QuestDB data lives in the `qdb_data` volume — survives container restarts.
- Binance dump gotcha: `transactTime` is **microseconds** (WS gives ms) → ×1000 = ns.
- QuestDB has no row DELETE; the repair unit is the day partition
  (`ALTER TABLE agg_trades DROP PARTITION LIST '2026-06-24';`).

## If ever continued
Phase 3 (dashboard) stayed optional. The honest financial path is not more
crypto tuning but pointing this harness at a market with an actual
information advantage — or accepting the system as what it is: infrastructure.
