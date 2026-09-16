"""Unit tests for the Abacus service.

Uses fakeredis's in-memory FakeServer, so these run with no real Redis
needed. "Two nodes" below means two separate FastAPI app instances that
both talk to fake Redis clients backed by the same FakeServer - that's
what actually proves cross-node consistency, rather than just testing
one app object twice.
"""
from __future__ import annotations

import asyncio

import fakeredis
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


def make_client_pair(server: fakeredis.FakeServer) -> tuple:
    """Two independent app instances simulating two compute nodes, both
    pointed at the same fake Redis backend.

    We set app.state.redis directly instead of letting the app's normal
    startup do it, because httpx's ASGITransport (used below) doesn't run
    FastAPI's startup/shutdown lifespan. In production, uvicorn does run
    it, and that's what creates the real Redis connection.
    """

    def _factory():
        return fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)

    app1 = create_app(node_id="node1", redis_factory=_factory)
    app1.state.redis = _factory()
    app2 = create_app(node_id="node2", redis_factory=_factory)
    app2.state.redis = _factory()
    return app1, app2


@pytest.fixture
def shared_server() -> fakeredis.FakeServer:
    return fakeredis.FakeServer()


@pytest.mark.asyncio
async def test_add_and_get_sum(shared_server):
    app1, _ = make_client_pair(shared_server)
    transport = ASGITransport(app=app1)
    async with AsyncClient(transport=transport, base_url="http://node1") as client:
        resp = await client.post("/abacus/number", json={"number": 5})
        assert resp.status_code == 201
        assert resp.json()["sum"] == 5.0

        resp = await client.post("/abacus/number", json={"number": 2.5})
        assert resp.json()["sum"] == 7.5

        resp = await client.get("/abacus/sum")
        assert resp.json()["sum"] == 7.5


@pytest.mark.asyncio
async def test_reset(shared_server):
    app1, _ = make_client_pair(shared_server)
    transport = ASGITransport(app=app1)
    async with AsyncClient(transport=transport, base_url="http://node1") as client:
        await client.post("/abacus/number", json={"number": 42})
        resp = await client.delete("/abacus/sum")
        assert resp.json()["sum"] == 0.0
        resp = await client.get("/abacus/sum")
        assert resp.json()["sum"] == 0.0


@pytest.mark.asyncio
async def test_negative_numbers_supported(shared_server):
    app1, _ = make_client_pair(shared_server)
    transport = ASGITransport(app=app1)
    async with AsyncClient(transport=transport, base_url="http://node1") as client:
        await client.post("/abacus/number", json={"number": 10})
        resp = await client.post("/abacus/number", json={"number": -3})
        assert resp.json()["sum"] == 7.0


@pytest.mark.asyncio
async def test_sum_is_zero_before_anything_is_posted(shared_server):
    """Edge case: a brand new deployment has no Redis key yet."""
    app1, _ = make_client_pair(shared_server)
    transport = ASGITransport(app=app1)
    async with AsyncClient(transport=transport, base_url="http://node1") as client:
        resp = await client.get("/abacus/sum")
        assert resp.json()["sum"] == 0.0


@pytest.mark.asyncio
async def test_missing_or_wrong_type_number_is_rejected(shared_server):
    """Edge case: malformed request bodies should fail validation (422),
    not crash the server or silently do nothing."""
    app1, _ = make_client_pair(shared_server)
    transport = ASGITransport(app=app1)
    async with AsyncClient(transport=transport, base_url="http://node1") as client:
        resp = await client.post("/abacus/number", json={})
        assert resp.status_code == 422

        resp = await client.post("/abacus/number", json={"number": "not-a-number"})
        assert resp.status_code == 422


@pytest.mark.asyncio
async def test_nan_and_infinity_are_rejected(shared_server):
    """Edge case: Python's json module (unlike the JSON spec) accepts the
    literals NaN/Infinity/-Infinity, and Redis's INCRBYFLOAT does not - so
    without this check, one of these values would crash the request with a
    raw Redis error instead of a clean 422. httpx's own json= helper refuses
    to encode these values at all, so we send the raw bytes to bypass it and
    prove the server itself rejects them, not just the client library."""
    app1, _ = make_client_pair(shared_server)
    transport = ASGITransport(app=app1)
    async with AsyncClient(transport=transport, base_url="http://node1") as client:
        for literal in ("NaN", "Infinity", "-Infinity"):
            resp = await client.post(
                "/abacus/number",
                content=f'{{"number": {literal}}}'.encode(),
                headers={"Content-Type": "application/json"},
            )
            assert resp.status_code == 422


@pytest.mark.asyncio
async def test_two_nodes_share_consistent_state(shared_server):
    """A POST on node1 must be immediately visible via GET on node2, and
    vice versa: correctness must not depend on which node you talk to."""
    app1, app2 = make_client_pair(shared_server)
    async with (
        AsyncClient(transport=ASGITransport(app=app1), base_url="http://node1") as c1,
        AsyncClient(transport=ASGITransport(app=app2), base_url="http://node2") as c2,
    ):
        await c1.delete("/abacus/sum")

        await c1.post("/abacus/number", json={"number": 10})
        resp = await c2.get("/abacus/sum")
        assert resp.json()["sum"] == 10.0

        await c2.post("/abacus/number", json={"number": 5})
        resp = await c1.get("/abacus/sum")
        assert resp.json()["sum"] == 15.0

        await c2.delete("/abacus/sum")
        resp = await c1.get("/abacus/sum")
        assert resp.json()["sum"] == 0.0


@pytest.mark.asyncio
async def test_concurrent_adds_across_two_nodes_are_exact(shared_server):
    """The core correctness guarantee: fire many concurrent POSTs split
    across two node instances and confirm the final sum is exact, with
    no lost updates from interleaving."""
    app1, app2 = make_client_pair(shared_server)
    async with (
        AsyncClient(transport=ASGITransport(app=app1), base_url="http://node1") as c1,
        AsyncClient(transport=ASGITransport(app=app2), base_url="http://node2") as c2,
    ):
        await c1.delete("/abacus/sum")

        n = 200
        numbers = [1.5 for _ in range(n)]
        expected = sum(numbers)

        async def post(i: int, value: float):
            client = c1 if i % 2 == 0 else c2
            await client.post("/abacus/number", json={"number": value})

        await asyncio.gather(*(post(i, v) for i, v in enumerate(numbers)))

        resp1 = await c1.get("/abacus/sum")
        resp2 = await c2.get("/abacus/sum")
        assert resp1.json()["sum"] == pytest.approx(expected)
        assert resp2.json()["sum"] == pytest.approx(expected)
