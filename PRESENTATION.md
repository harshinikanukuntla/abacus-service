# Presentation commands

Every command you'd need to demo this project, in the order you'd likely
run them. Copy-paste ready. Run everything from the project root.

## 0. One-time setup (do this before the interview, not during)

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

Creates a virtual environment and installs FastAPI, Redis's Python client,
and the test/demo tooling into it.

## 1. Run the unit tests

```bash
./.venv/bin/pytest -v
```

Runs all 8 tests against a fake, in-memory Redis, no real Redis needed.
Good opener, shows the logic is verified before you even touch a live
server. Takes under a second.

## 2. Run multiple nodes locally, no Docker

```bash
./scripts/run_nodes.sh          # starts 2 nodes, ports 8001 and 8002
./scripts/run_nodes.sh 5        # starts 5 nodes, ports 8001 to 8005
```

Starts a local Redis (if one isn't already running) plus however many
`uvicorn` processes you asked for, all pointed at that one Redis. Leave
this running in its own terminal, it prints the URLs it started and
blocks until you press Ctrl+C, which shuts down everything it started.

### 2a. Prove two nodes agree, by hand

Run these in a second terminal while step 2 is still running:

```bash
curl -X POST http://localhost:8001/abacus/number -H 'Content-Type: application/json' -d '{"number": 5}'
curl -X POST http://localhost:8002/abacus/number -H 'Content-Type: application/json' -d '{"number": 7}'
curl http://localhost:8001/abacus/sum
curl http://localhost:8002/abacus/sum
```

Both GETs should return `12.0`, even though node1 only ever saw the `5`
and node2 only ever saw the `7`. That's the whole point, made visible.

```bash
curl -X DELETE http://localhost:8001/abacus/sum
curl http://localhost:8002/abacus/sum
```

Resets via node1, reads via node2, should immediately show `0.0`.

### 2b. Prove it automatically, with real concurrency

```bash
./.venv/bin/python scripts/load_test.py --nodes-file .demo_nodes --requests 500
```

Fires 500 concurrent POSTs, split across every node `run_nodes.sh`
started (reads their URLs from `.demo_nodes`), then checks every node
reports the exact same, correct total afterward, and that a reset from
one node is instantly visible from another. Bump `--requests 5000` for a
bigger number if you want a more dramatic run.

## 3. Run it with Docker instead

```bash
docker compose up --build
```

This one command starts **four** containers at once: `redis`, plus
`node1`, `node2`, and `node3`, reachable on ports 8001, 8002, and 8003.
It's not one node, it's already three, all sharing that one Redis
container. Leave this running in its own terminal, it streams logs from
all four containers live.

If Docker Desktop isn't running yet:

```bash
open -a Docker
docker info      # confirm it's ready before running compose
```

### 3a. Prove three nodes agree, including one that was never posted to

This is the strongest demo moment, run these in a second terminal:

```bash
curl -X POST http://localhost:8001/abacus/number -H 'Content-Type: application/json' -d '{"number": 5}'
curl -X POST http://localhost:8002/abacus/number -H 'Content-Type: application/json' -d '{"number": 7}'
curl http://localhost:8003/abacus/sum
```

That last line hits **node3**, which never received either POST
directly, and it should still return `{"sum": 12.0, "node": "node3"}`.
That's the proof that consistency comes from the shared Redis, not from
anything the nodes are doing between themselves.

```bash
curl -X DELETE http://localhost:8003/abacus/sum
curl http://localhost:8001/abacus/sum
curl http://localhost:8002/abacus/sum
```

Reset from node3 this time, confirm node1 and node2 both immediately
see `0.0`.

### 3b. Automated load test against the Docker containers

```bash
./.venv/bin/python scripts/load_test.py --nodes http://localhost:8001,http://localhost:8002,http://localhost:8003 --requests 1000 --concurrency 200
```

Same idea as 2b, but against all three real containers instead of local
processes. Confirms it holds up under concurrent load in the
containerized setup too, not just the local one.

### 3c. Peeking under the hood while Docker is running

```bash
docker compose ps
```
Lists all four containers and their status.

```bash
docker compose logs -f node1
```
Tails just node1's logs live, useful to show requests arriving in real
time if someone asks "how do you know it's actually node1 answering."

```bash
docker exec -it abacus-service-redis-1 redis-cli GET abacus:sum
```
Looks directly at the value sitting in Redis, bypassing the app
entirely, good for proving the number really lives in Redis and not
somewhere in the app.

### 3d. Shutting Docker down afterward

```bash
docker compose down
```
Stops and removes the four containers.

```bash
docker compose down -v
```
Same, but also wipes Redis's saved data, use this if you want the next
`docker compose up` to start from a completely clean `0`.

## 4. Health and docs endpoints, quick sanity checks

```bash
curl http://localhost:8001/healthz
```
Returns `{"status": "ok", "node": "node1"}`, this is what an
orchestrator like Kubernetes would poll to know a node is alive.

```bash
open http://localhost:8001/docs
```
Opens FastAPI's auto-generated interactive API docs in a browser, handy
if someone wants to see the request/response shapes without reading code.

## Quick answer if someone asks "how many nodes are running right now"

- `docker compose up` by itself: **3 nodes** (node1, node2, node3), not 1.
- `./scripts/run_nodes.sh`: **2 nodes** by default, or however many you
  passed as an argument.
- Both can't use the same ports at the same time. If Docker is already up
  on 8001 to 8003, run the local script on a different range:
  ```bash
  BASE_PORT=9000 ./scripts/run_nodes.sh 10
  ```
