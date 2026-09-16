# Abacus Service

A FastAPI microservice that keeps a running sum, correctly, even when
many copies of it run at once across different machines/containers.

```
POST   /abacus/number   {"number": N}   -> adds N to the running sum
GET    /abacus/sum                       -> current running sum
DELETE /abacus/sum                       -> reset sum to 0
```

## How it stays correct across many nodes

Each server instance holds no state of its own. Every request is just
relayed to one shared Redis instance, which does the actual work with a
single atomic command:

| Endpoint               | Redis command        |
|-------------------------|-----------------------|
| `POST /abacus/number`   | `INCRBYFLOAT key N`   |
| `GET /abacus/sum`       | `GET key`             |
| `DELETE /abacus/sum`    | `SET key 0`           |

Redis processes commands one at a time, so no matter how many instances
are running or how many requests land at the same instant, there's only
ever one number and it's always updated correctly. No locks, no syncing
between nodes needed - because there's nothing to sync, there's only
one copy of the data.

(This is also why each instance doesn't keep its own local counter and
periodically merge it with the others - that would be faster, but a
`GET` could return a stale value depending on which node answers.)

## Project layout

```
app/main.py          FastAPI app (factory: create_app())
tests/test_api.py    Unit tests, no real Redis needed (uses fakeredis)
scripts/run_nodes.sh Boots local Redis + N node processes for a live demo
scripts/load_test.py Fires concurrent requests across nodes, checks correctness
Dockerfile, docker-compose.yml   Redis + 3 nodes in containers
```

## Run the unit tests

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/pytest -v
```

## Demo: run multiple nodes locally

Starts a local Redis (if one isn't already running) plus however many
node processes you ask for, all sharing that Redis:

```bash
./scripts/run_nodes.sh          # 2 nodes, ports 8001-8002
./scripts/run_nodes.sh 50       # 50 nodes, ports 8001-8050
```

Leave that running. In a second terminal, either poke it by hand:

```bash
curl -X POST http://localhost:8001/abacus/number -H 'Content-Type: application/json' -d '{"number": 5}'
curl -X POST http://localhost:8002/abacus/number -H 'Content-Type: application/json' -d '{"number": 7}'
curl http://localhost:8001/abacus/sum   # -> {"sum": 12.0, "node": "node1"}
curl http://localhost:8002/abacus/sum   # -> {"sum": 12.0, "node": "node2"} - same value, different node
```

...or run the automated load test, which fires concurrent requests
split across every node and checks they all end up with the exact same,
correct total:

```bash
./.venv/bin/python scripts/load_test.py --nodes-file .demo_nodes --requests 500
```

Press `Ctrl+C` in the `run_nodes.sh` terminal when you're done - it
stops every node it started (and Redis, if it started that too).

## Run it with Docker instead

```bash
docker compose up --build
```

Starts Redis + 3 nodes as containers, reachable at `localhost:8001`,
`:8002`, `:8003`. Test the same way as above, or:

```bash
./.venv/bin/python scripts/load_test.py --nodes http://localhost:8001,http://localhost:8002,http://localhost:8003
```
