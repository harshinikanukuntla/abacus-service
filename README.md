# Abacus Service

A FastAPI microservice that keeps a running sum. It's built to stay correct
even when several copies of it are running at the same time on different
machines or containers, which was the main challenge in the task below.

## Task #2

FastAPI Microservice / consistency

Using FastAPI and python3.12+ you need to develop a micro-service with the following APIs :

POST …/abacus/number -d ‘{“number”: N}’ - adds a number N to the current running sum
GET …/abacus/sum - get the current running sum
DELETE .../abacus/sum - reset the running sum to 0.
Service needs to handle:

many req/minute (e.g. 10-100-1000 ) for POST API
few req/minute (e.g. 1-10) for GET api. 
Correctness of operation when deployed on N compute nodes/containers (e.g. 2-10-100). 
 

Storage layer is up to the candidate. Solution should strive for maximum consistency for the sum API.

Need to be able to demonstrate/simulate at least 2 nodes running locally in terminal/console

## What this covers

| What was asked | How it's handled |
|---|---|
| POST adds N to the sum | `INCRBYFLOAT` on the Redis key, done inside Redis in one step |
| GET returns the current sum | plain Redis `GET`, returns 0 if nothing was ever posted |
| DELETE resets sum to 0 | plain Redis `SET key 0` |
| Handle 10 to 1000+ POSTs a minute | a single Redis instance handles far more than that; verified with a load test firing thousands of concurrent requests |
| Handle a handful of GETs a minute | trivial for the same reason, a GET is one Redis read |
| Stay correct across 2 to 100 nodes | every node is stateless and talks to the same Redis, so there's one number, not many to keep in sync |
| Pick a storage layer | went with Redis, atomic commands and speed make it a good fit for a shared counter |
| Maximise consistency | since every write and read goes through the same Redis key using atomic commands, every node sees the same value at every point in time |
| Demonstrate 2+ nodes locally | `scripts/run_nodes.sh` starts as many local node processes as you want against one Redis, `scripts/load_test.py` proves they agree |

## Why it stays correct with multiple nodes running

None of the app instances keep the sum in memory. Every request just gets
passed straight through to one shared Redis instance, and Redis is what
actually does the work:

| Endpoint | Redis command |
|---|---|
| `POST /abacus/number` | `INCRBYFLOAT key N` |
| `GET /abacus/sum` | `GET key` |
| `DELETE /abacus/sum` | `SET key 0` |

Redis only ever runs one command at a time, so two nodes adding a number at
the exact same moment can't step on each other or lose an update. There's
nothing to lock and nothing to sync between nodes, because there's only one
copy of the number to begin with. This is stronger (and simpler) than having
each node keep its own counter and merge them later, which would let a GET
return a stale answer depending on which node picked it up.

## A few things the API also handles

- Numbers can be positive, negative or decimals.
- A missing or non numeric `number` field returns a 422, not a crash.
- NaN and Infinity are rejected the same way, since Redis can't do math with them.
- There's a `/healthz` endpoint each node responds to, useful for checking it's up.
- Every response includes which node answered it, handy for seeing requests bounce between instances during testing.

## Project layout

```
app/main.py          the FastAPI app
tests/test_api.py    unit tests, no real Redis needed
scripts/run_nodes.sh starts N local node processes against one shared Redis
scripts/load_test.py fires concurrent requests at those nodes and checks the result
Dockerfile, docker-compose.yml   Redis + 3 nodes as containers
```

## Running it

Set up the environment and run the unit tests first:

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/pytest -v
```

To see multiple nodes running locally, start however many you want:

```bash
./scripts/run_nodes.sh          # 2 nodes, ports 8001 and 8002
./scripts/run_nodes.sh 50       # 50 nodes, ports 8001 to 8050
```

Leave that running, then in a second terminal try it by hand:

```bash
curl -X POST http://localhost:8001/abacus/number -H 'Content-Type: application/json' -d '{"number": 5}'
curl -X POST http://localhost:8002/abacus/number -H 'Content-Type: application/json' -d '{"number": 7}'
curl http://localhost:8001/abacus/sum   # {"sum": 12.0, "node": "node1"}
curl http://localhost:8002/abacus/sum   # {"sum": 12.0, "node": "node2"}, same value, different node
```

Or let the load test do it for you. It fires several hundred (or thousand)
concurrent requests split across every node and checks they all land on the
exact same total:

```bash
./.venv/bin/python scripts/load_test.py --nodes-file .demo_nodes --requests 500
```

Ctrl+C in the `run_nodes.sh` terminal stops everything it started, Redis
included if it started that too.

To run the same thing in Docker instead:

```bash
docker compose up --build
```

That brings up Redis plus 3 nodes, reachable at localhost:8001, 8002 and
8003. Test it the same way, or point the load test at those ports:

```bash
./.venv/bin/python scripts/load_test.py --nodes http://localhost:8001,http://localhost:8002,http://localhost:8003
```
