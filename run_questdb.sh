#!/usr/bin/env bash
# Start QuestDB (Phase 0) on the bundled Temurin 25 JDK — no system install needed.
# Console + SQL:  http://localhost:9000
# Ingest (ILP/HTTP): POST to :9000/write   |   Postgres wire: :8812
set -euo pipefail
DIR="$HOME/crypto-quant"
export JAVA_HOME="$DIR/runtime/jdk25/Contents/Home"
mkdir -p "$DIR/qdb-data"
echo "QuestDB starting -> http://localhost:9000  (Ctrl-C to stop)"
exec "$JAVA_HOME/bin/java" -p "$DIR/runtime/questdb/questdb.jar" \
  -m io.questdb/io.questdb.ServerMain -d "$DIR/qdb-data"
