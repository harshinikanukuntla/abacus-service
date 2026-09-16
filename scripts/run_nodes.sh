#!/usr/bin/env bash
# Starts a local Redis (if one isn't already running) plus however many
# abacus-service node processes you ask for, all pointed at that same Redis.
#
# Usage:
#   ./scripts/run_nodes.sh                     # 2 nodes on ports 8001-8002
#   ./scripts/run_nodes.sh 50                  # 50 nodes on ports 8001-8050
#   BASE_PORT=9000 ./scripts/run_nodes.sh 10   # 10 nodes starting at 9000
#
# Node URLs are written one per line to .demo_nodes, which
# scripts/load_test.py can read via --nodes-file .demo_nodes
set -euo pipefail

cd "$(dirname "$0")/.."

NUM_NODES="${1:-2}"
BASE_PORT="${BASE_PORT:-8001}"
REDIS_PORT="${REDIS_PORT:-6379}"
PIDFILE=".demo_pids"
NODES_FILE=".demo_nodes"

: > "$PIDFILE"
: > "$NODES_FILE"

cleanup() {
  echo
  echo "Stopping demo processes..."
  while read -r pid; do
    [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
  done < "$PIDFILE"
  rm -f "$PIDFILE" "$NODES_FILE"
}
trap cleanup EXIT INT TERM

if ! redis-cli -p "$REDIS_PORT" ping >/dev/null 2>&1; then
  echo "No Redis found on port $REDIS_PORT, starting one..."
  redis-server --port "$REDIS_PORT" --save "" --appendonly no > redis.log 2>&1 &
  echo $! >> "$PIDFILE"
  for _ in $(seq 1 20); do
    redis-cli -p "$REDIS_PORT" ping >/dev/null 2>&1 && break
    sleep 0.2
  done
fi
redis-cli -p "$REDIS_PORT" set abacus:sum 0 >/dev/null
echo "Redis ready on port $REDIS_PORT (sum reset to 0)."

if [ ! -d .venv ]; then
  echo "Creating virtualenv..."
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -r requirements.txt
fi

echo "Starting $NUM_NODES node(s) on ports ${BASE_PORT}..$((BASE_PORT + NUM_NODES - 1)) ..."
for i in $(seq 0 $((NUM_NODES - 1))); do
  PORT=$((BASE_PORT + i))
  NAME="node$((i + 1))"
  REDIS_URL="redis://localhost:${REDIS_PORT}/0" NODE_ID="$NAME" \
    ./.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "$PORT" > "${NAME}.log" 2>&1 &
  echo $! >> "$PIDFILE"
  echo "http://localhost:${PORT}" >> "$NODES_FILE"
done

echo "Waiting for all nodes to come up..."
READY=0
for _ in $(seq 1 60); do
  READY=1
  while read -r url; do
    curl -sf "${url}/healthz" >/dev/null 2>&1 || READY=0
  done < "$NODES_FILE"
  [ "$READY" = "1" ] && break
  sleep 0.3
done
[ "$READY" = "1" ] || echo "WARNING: not all nodes came up in time — check the *.log files."

echo
echo "$NUM_NODES node(s) are up:"
cat "$NODES_FILE"
echo
echo "Try it, e.g.:"
FIRST_URL=$(head -n 1 "$NODES_FILE")
echo "  curl -X POST $FIRST_URL/abacus/number -H 'Content-Type: application/json' -d '{\"number\": 5}'"
echo "  curl $FIRST_URL/abacus/sum"
echo
echo "Or run the automated consistency load test in another terminal:"
echo "  ./.venv/bin/python scripts/load_test.py --nodes-file $NODES_FILE --requests 500"
echo
echo "Press Ctrl+C to stop everything (and Redis, if this script started it)."
wait
