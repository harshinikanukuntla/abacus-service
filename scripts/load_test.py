#!/usr/bin/env python3
"""Concurrency/consistency demo for the Abacus service.

Fires a burst of concurrent POST /abacus/number requests, split roughly
evenly across all the given node URLs, tracking the expected total in
this script. Then reads GET /abacus/sum from *every* node and checks
they all agree with each other and with the expected total. Finally
resets via one node and confirms the reset is visible immediately from
another node.

Usage:
    # against nodes started by scripts/run_nodes.sh:
    python scripts/load_test.py --nodes-file .demo_nodes --requests 500

    # or list URLs directly (e.g. Docker Compose ports):
    python scripts/load_test.py --nodes http://localhost:8001,http://localhost:8002
"""
from __future__ import annotations

import argparse
import asyncio
import random
import time

import httpx


async def post_number(client: httpx.AsyncClient, base_url: str, number: float) -> None:
    resp = await client.post(f"{base_url}/abacus/number", json={"number": number})
    resp.raise_for_status()


async def get_sum(client: httpx.AsyncClient, base_url: str) -> float:
    resp = await client.get(f"{base_url}/abacus/sum")
    resp.raise_for_status()
    return resp.json()["sum"]


async def reset_sum(client: httpx.AsyncClient, base_url: str) -> float:
    resp = await client.delete(f"{base_url}/abacus/sum")
    resp.raise_for_status()
    return resp.json()["sum"]


async def main(node_urls: list[str], num_requests: int, concurrency: int) -> None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        print(f"Resetting sum via {node_urls[0]} ...")
        await reset_sum(client, node_urls[0])

        numbers = [round(random.uniform(-50, 50), 2) for _ in range(num_requests)]
        expected_total = round(sum(numbers), 2)

        shown = node_urls if len(node_urls) <= 6 else [*node_urls[:3], "...", *node_urls[-2:]]
        print(
            f"Firing {num_requests} concurrent POSTs "
            f"(max {concurrency} in flight) across {len(node_urls)} node(s): {shown}"
        )

        sem = asyncio.Semaphore(concurrency)

        async def bound_post(i: int, n: float) -> None:
            async with sem:
                base_url = node_urls[i % len(node_urls)]
                await post_number(client, base_url, n)

        start = time.perf_counter()
        await asyncio.gather(*(bound_post(i, n) for i, n in enumerate(numbers)))
        elapsed = time.perf_counter() - start

        print(f"Done in {elapsed:.2f}s ({num_requests / elapsed:.0f} req/s).")

        sums = {url: await get_sum(client, url) for url in node_urls}
        print("Sum reported by each node:")
        for url, s in sums.items():
            print(f"  {url}: {s}")

        print(f"Expected total: {expected_total}")

        all_agree = len(set(round(v, 2) for v in sums.values())) == 1
        matches_expected = all(
            abs(v - expected_total) < 1e-6 for v in sums.values()
        )

        if all_agree and matches_expected:
            print("PASS: all nodes agree and match the expected total exactly.")
        else:
            print("FAIL: nodes disagree or total is wrong.")
            raise SystemExit(1)

        # Reset from one node, verify the *other* node sees it immediately.
        reset_from = node_urls[0]
        read_from = node_urls[-1]
        print(f"\nResetting via {reset_from}, reading back via {read_from} ...")
        await reset_sum(client, reset_from)
        after_reset = await get_sum(client, read_from)
        if after_reset == 0.0:
            print(f"PASS: {read_from} immediately sees the reset (sum={after_reset}).")
        else:
            print(f"FAIL: {read_from} sees stale sum={after_reset} after reset.")
            raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--nodes",
        help="Comma-separated node URLs, e.g. http://localhost:8001,http://localhost:8002",
    )
    parser.add_argument(
        "--nodes-file",
        help="File with one node URL per line (e.g. .demo_nodes from run_nodes.sh)",
    )
    parser.add_argument("--requests", type=int, default=500)
    parser.add_argument("--concurrency", type=int, default=100)
    args = parser.parse_args()

    if args.nodes_file:
        with open(args.nodes_file) as f:
            urls = [line.strip() for line in f if line.strip()]
    elif args.nodes:
        urls = [u.strip() for u in args.nodes.split(",") if u.strip()]
    else:
        urls = ["http://localhost:8001", "http://localhost:8002"]

    if len(urls) < 2:
        raise SystemExit("Need at least 2 node URLs to test cross-node consistency.")

    asyncio.run(main(urls, args.requests, args.concurrency))
