# Abacus Service

A FastAPI microservice exposing a running-sum counter, designed to stay
correct when deployed across many stateless compute nodes.

```
POST   /abacus/number   {"number": N}   -> adds N to the running sum
GET    /abacus/sum                       -> current running sum
DELETE /abacus/sum                       -> reset sum to 0
```

## Design: how consistency is achieved across N nodes

The app process holds **no counter state of its own**. Every node —
whether there are 2 or 200 — does the same thing: it opens a pooled
connection to one shared Redis instance and issues a single Redis
command per request:

| Endpoint              | Redis command      | Why it's safe concurrently |
|------------------------|---------------------|------------------------------|
| `POST /abacus/number`  | `INCRBYFLOAT key N` | Atomic read-modify-write done *inside* Redis in one step |
| `GET /abacus/sum`      | `GET key`           | Single read of the current value |
| `DELETE /abacus/sum`   | `SET key 0`         | Atomic overwrite |

Redis is single-threaded for command execution: it processes commands
from every connected client one at a time, in some serial order. That
means concurrent `INCRBYFLOAT` calls from any number of nodes can never
interleave or lose an update — each one is applied completely before
the next begins. There is no application-level locking, no
compare-and-swap loop, and no per-node cache to reconcile, so there is
nothing that can drift out of sync between nodes. This gives
**linearizable consistency** for the sum: at any instant, every node
asking "what is the sum right now" gets the same, correct answer.

This is why the design deliberately avoids the tempting alternative of
letting each node keep a local in-memory counter and periodically
sync/merge them — that approach trades consistency for throughput and
would let `GET /abacus/sum` return a stale or incorrect value depending
on which node answers and how recently it synced. Since the prompt asks
to *maximize* consistency, that tradeoff was rejected in favor of a
single shared source of truth.

### Throughput

A single Redis instance comfortably handles tens of thousands of
simple commands per second, so even 1000 POSTs/minute (~17/sec) fanned
out over 100 nodes is negligible load — the bottleneck, if any, would
be network/connection overhead per node, addressed here with a pooled
async Redis client (`redis.asyncio`) reused across all requests in a
process.

### Trade-offs / what you'd add for production hardening

- **Durability**: enabled here via Redis AOF (`--appendonly yes` in
  `docker-compose.yml`) so a Redis restart doesn't lose the sum.
- **High availability**: a single Redis instance is a single point of
  failure. For HA you'd add Redis replicas (Sentinel) or Redis Cluster.
  Note the CAP trade-off: async replication means a failover could lose
  the last acknowledged write, trading a sliver of durability for
  availability; if you need zero lost writes across a failover you'd
  reach for `WAIT` (block until replicas ack) or a transactional
  RDBMS (`UPDATE counter SET value = value + N`, also atomic and
  consistent, at lower throughput than Redis).
- **Multi-region**: this design assumes one primary Redis a request can
  reach with low latency; true multi-region strong consistency would
  need a consensus-backed store (e.g. CockroachDB) at a latency cost.

## Project layout

```
app/main.py          FastAPI app (factory: create_app())
tests/test_api.py     Unit tests using fakeredis (no external deps)
scripts/run_nodes.sh  Boots local Redis + 2 node processes for a live demo
scripts/load_test.py  Fires concurrent load across the 2 nodes and verifies correctness
Dockerfile, docker-compose.yml   Container deployment (Redis + 3 nodes)
```

## Running the unit tests

No external services required — uses fakeredis:

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/pytest -v
```

## Demonstrating 2+ nodes locally (terminal)

This starts a local Redis (if one isn't already running on 6379) plus
two independent `uvicorn` node processes on ports 8001 and 8002, both
pointed at that same Redis:

```bash
./scripts/run_nodes.sh
```

Leave that running, then in another terminal drive it manually:

```bash
curl -X POST http://localhost:8001/abacus/number -H 'Content-Type: application/json' -d '{"number": 5}'
curl -X POST http://localhost:8002/abacus/number -H 'Content-Type: application/json' -d '{"number": 7}'
curl http://localhost:8001/abacus/sum   # -> {"sum": 12.0, "node": "node1"}
curl http://localhost:8002/abacus/sum   # -> {"sum": 12.0, "node": "node2"} (same value, different node)
curl -X DELETE http://localhost:8001/abacus/sum
curl http://localhost:8002/abacus/sum   # -> {"sum": 0.0, "node": "node2"}
```

...or run the automated load test, which fires hundreds of concurrent
POSTs split across both nodes and asserts the final sum is exact on
both, plus verifies a reset from one node is immediately visible on
the other:

```bash
./.venv/bin/python scripts/load_test.py \
  --node1 http://localhost:8001 --node2 http://localhost:8002 \
  --requests 500 --concurrency 100
```

Press `Ctrl+C` in the `run_nodes.sh` terminal to stop both nodes (and
Redis, if the script started it).

## Running with Docker (Redis + 3 nodes)

```bash
docker compose up --build
```

Nodes are then reachable at `localhost:8001`, `:8002`, `:8003`, all
sharing the one `redis` service.
