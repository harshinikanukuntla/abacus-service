"""Abacus microservice: a running-sum counter shared across many server instances.

Every instance is "dumb" - it holds no state of its own and just relays each
request to one shared Redis instance, which does the actual add/read/reset.
Redis only ever does one thing at a time, so no matter how many instances are
running or how many requests hit them at once, there's a single number and
it's always updated correctly.
"""

from __future__ import annotations

import math
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator, Callable

import redis.asyncio as redis
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
SUM_KEY = os.environ.get("ABACUS_REDIS_KEY", "abacus:sum")
NODE_ID = os.environ.get("NODE_ID", f"pid-{os.getpid()}")


class NumberIn(BaseModel):
    number: float = Field(..., description="Number to add to the running sum")

    @field_validator("number")
    @classmethod
    def must_be_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("number must be finite (NaN/Infinity aren't allowed)")
        return value


class SumOut(BaseModel):
    sum: float
    node: str = Field(..., description="Which node handled this request")


RedisFactory = Callable[[], "redis.Redis"]


def _default_redis_factory() -> "redis.Redis":
    return redis.from_url(REDIS_URL, decode_responses=True)


def create_app(
    node_id: str = NODE_ID,
    redis_factory: RedisFactory = _default_redis_factory,
) -> FastAPI:
    """Build the app. Tests pass in a fake redis_factory instead of a real one,
    so they can run without a real Redis server."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.redis = redis_factory()
        await app.state.redis.ping()
        try:
            yield
        finally:
            await app.state.redis.aclose()

    app = FastAPI(title="Abacus Service", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def on_bad_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default 422 body echoes back the rejected value under
        # "input", e.g. {"input": nan} - but NaN/Infinity can't be put into
        # a JSON response either, which turns a bad request into a 500
        # instead of a 422. Drop that field; loc/msg/type are enough to
        # tell the caller what was wrong.
        errors = [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": errors})

    @app.post("/abacus/number", response_model=SumOut, status_code=201)
    async def add_number(payload: NumberIn) -> SumOut:
        # INCRBYFLOAT happens entirely inside Redis in one step, so two
        # instances adding a number "at the same time" can never step on
        # each other or lose an update.
        new_sum = await app.state.redis.incrbyfloat(SUM_KEY, payload.number)
        return SumOut(sum=float(new_sum), node=node_id)

    @app.get("/abacus/sum")
    async def get_sum() -> SumOut:
        value = await app.state.redis.get(SUM_KEY)
        return SumOut(sum=float(value) if value is not None else 0.0, node=node_id)

    @app.delete("/abacus/sum")
    async def reset_sum() -> SumOut:
        await app.state.redis.set(SUM_KEY, 0)
        return SumOut(sum=0.0, node=node_id)

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok", "node": node_id}

    return app


app = create_app()
