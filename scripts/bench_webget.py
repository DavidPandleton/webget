"""Repeatable performance benchmark for webget.

Runs against the LOCAL deterministic test server (no external sites):
  - scales: 10, 100, 500 URLs
  - strategies: http (fast path), auto (ladder w/o crawl4ai), auto+cache
  - metrics: total wall time, p50/p95/p99 latency, success rate,
    fallback rate, cache hit rate, requests/sec

Usage:
    python scripts/bench_webget.py [--scale 10,100,500] [--strategy http,auto]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from http_server import TestServer

import webget_cli as webget


async def run_scale(server, urls, strategy, use_cache, max_concurrency=None):
    """Fetch all urls in one bounded batch, return summary metrics."""
    t0 = time.perf_counter()
    res = await webget.scrape_many(
        urls,
        max_chars=2000,
        per_url_timeout=15,
        no_cache=not use_cache,
        strategy=strategy,
        max_concurrency=max_concurrency,
    )
    wall = time.perf_counter() - t0
    statuses = {u: res.get(u, {}).get("status", "missing") for u in urls}
    methods = {u: res.get(u, {}).get("method", "") for u in urls}
    cached = sum(1 for u in urls if res.get(u, {}).get("cached"))
    success = sum(1 for s in statuses.values() if s == "success")
    fallback = sum(1 for m in methods.values() if m not in ("http", "cache"))
    return {
        "n": len(urls),
        "wall_s": round(wall, 3),
        "req_per_s": round(len(urls) / wall, 1) if wall else 0,
        "success": f"{success}/{len(urls)}",
        "success_rate": round(success / len(urls) * 100, 1) if urls else 0,
        "fallback_count": fallback,
        "cached_count": cached,
    }


def make_urls(server, n, pattern="normal"):
    """n unique URLs against the deterministic server."""
    urls = []
    for i in range(n):
        p = f"/{pattern}"
        if i % 5 == 1:
            p = "/login"
        elif i % 5 == 2:
            p = "/403"
        elif i % 5 == 3:
            p = "/redirect"
        elif i % 5 == 4:
            p = "/gzip"
        urls.append(server.url(f"{p}?i={i}"))
    return urls


def pct(values, p):
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * p))]


async def latency_sample(server, urls, strategy, use_cache, max_concurrency=None):
    """Per-URL latency distribution: fetch each URL individually (bounded
    concurrency) and return p50/p95/p99 in ms."""
    latencies = []
    sem = asyncio.Semaphore(max_concurrency or 10)

    async def one(url):
        async with sem:
            t0 = time.perf_counter()
            await webget.scrape_many(
                [url],
                max_chars=2000,
                per_url_timeout=15,
                no_cache=not use_cache,
                strategy=strategy,
            )
            latencies.append((time.perf_counter() - t0) * 1000)

    await asyncio.gather(*(one(u) for u in urls))
    return {
        "p50_ms": round(pct(latencies, 0.50), 1),
        "p95_ms": round(pct(latencies, 0.95), 1),
        "p99_ms": round(pct(latencies, 0.99), 1),
    }


async def bench(server, scales, strategies, with_cache=False):
    print(f"{'scale':<6} {'strategy':<10} {'wall':>7} {'req/s':>7} "
          f"{'success':>9} {'fallback':>8} {'cached':>7} "
          f"{'p50':>7} {'p95':>7} {'p99':>7}")
    print("-" * 92)
    for n in scales:
        urls = make_urls(server, n)
        for strat in strategies:
            r = await run_scale(server, urls, strat, use_cache=with_cache)
            lat = await latency_sample(server, urls[:30], strat, use_cache=with_cache)
            print(f"{r['n']:<6} {strat:<10} {r['wall_s']:>7} {r['req_per_s']:>7} "
                  f"{r['success']:>9} {r['fallback_count']:>8} {r['cached_count']:>7} "
                  f"{lat['p50_ms']:>7} {lat['p95_ms']:>7} {lat['p99_ms']:>7}")
            # warm the cache for the cached run
            if with_cache:
                await webget.scrape_many(urls, max_chars=2000, no_cache=False, strategy=strat)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", default="10,100,500")
    ap.add_argument("--strategy", default="http,auto")
    ap.add_argument("--cache", action="store_true", help="also measure cache-hit runs")
    args = ap.parse_args()

    os.environ.setdefault("WEBGET_ALLOW_PRIVATE", "1")
    # isolate cache to a temp dir so the benchmark is repeatable
    tmp = tempfile.mkdtemp(prefix="webget-bench-")
    webget.CACHE_DIR = os.path.join(tmp, "cache")

    server = TestServer().start()
    try:
        scales = [int(s) for s in args.scale.split(",")]
        strategies = [s.strip() for s in args.strategy.split(",")]
        print(f"server: {server.host}:{server.port}  scales={scales}  strategies={strategies}\n")
        print("== cold cache ==")
        asyncio.run(bench(server, scales, strategies, with_cache=False))
        if args.cache:
            print("\n== warm cache (2nd run) ==")
            asyncio.run(bench(server, scales, strategies, with_cache=True))
    finally:
        server.stop()


if __name__ == "__main__":
    main()
