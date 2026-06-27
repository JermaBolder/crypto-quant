# crypto-quant — modern-stack learning build

Goal: learn the modern data/quant stack by building a real one. Money is a
*consequence* (skills, later funding-neutral carry), not the bet.

Roadmap: **0 data+DB ✓ · 1 streaming ✓ · 2 ML signals · 5 prod.**
The engine is data-source-agnostic, so the crypto-vs-FX choice is deferred to
the strategy phase.

## Architecture (Phase 1)
```
Binance WS ──producer──▶ Redis Stream "trades" ──consumer(group)──▶ QuestDB
```
Producer and consumer are decoupled in time: either can crash, restart, or lag
without taking the other down. The consumer ACKs an entry only *after* the DB
write succeeds, so nothing is lost on a crash (at-least-once delivery).

## Files
| file | what |
|---|---|
| `sources.py` | `Trade` + pluggable `TradeSource` (Binance now; Bybit/FX later). |
| `producer.py` | WS trades → Redis Stream (`XADD`, capped at ~100k entries). |
| `consumer_questdb.py` | Redis Stream → QuestDB via consumer group `cg_questdb`. |
| `qdb_sink.py` | QuestDB writer (line protocol over HTTP, stdlib only). |
| `run_questdb.sh` | start QuestDB on the bundled Temurin 25 JDK. |
| `stream_print.py` | 20-trade websocket smoke test (no DB). |
| `legacy/ingest.py` | retired Phase-0 monolith (WS→QuestDB in one loop). Do **not** run alongside the consumer — double-write. |
| `runtime/`, `qdb-data/` | bundled JDK+QuestDB, and QuestDB's data (big, regenerable). |

## Run the pipeline
```bash
# 1. infra
./run_questdb.sh                       # QuestDB  → http://localhost:9000
brew services start redis              # Redis bus (once; launchd keeps it up)

# 2. pipeline (two terminals, or background both)
.venv/bin/python producer.py           # WS → Redis Stream
.venv/bin/python consumer_questdb.py   # Redis Stream → QuestDB
#   append a number for a bounded test run, e.g.  producer.py 20

# 3. inspect
redis-cli XLEN trades
redis-cli XINFO GROUPS trades          # lag ~0 = consumer keeping up
curl -sG http://localhost:9000/exec --data-urlencode \
  "query=SELECT side, count(), sum(size) FROM trades"
```

## Stack notes (macOS arm64)
- QuestDB ships no mac binary → bundled JDK; 9.4.3 needs **Java 25** (class major 69).
- Redis + Homebrew installed for Phase 1 (Homebrew also unblocks Docker for Phase 5).
- `uv` + `markitdown` live in `~/.local/bin` (doc→markdown utility).

## Next — Phase 2: signals
A second consumer on the same stream computing a rolling order-flow metric
(delta), then ML features. Same bus, new consumer = fan-out.
