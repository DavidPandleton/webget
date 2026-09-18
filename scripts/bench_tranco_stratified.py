"""Stratified re-benchmark of webget fetch against the 2026-09-01 Tranco run.

Design goals:
  - Comparable to data/tranco/SUMMARY.md: same corpus source (Tranco top-10k),
    same per-URL timeout, same concurrency, same http-first strategy.
  - Stratified by rank decade so the rank->reachability curve can be compared
    band-for-band against the old run instead of only as one aggregate.
  - Fresh cache dir (not the shared ~/.cache/webget) so results are not
    polluted by the September run's cache, and so this run cannot pollute it.

Deliberately NOT changed from the old run: per_url_timeout=8s, concurrency=40,
group size 100. Changing those would confound the comparison (the old run's
#1 finding was that timeouts dominate, so a longer timeout would look like a
huge win that is really just a config change). Those are separate experiments.

Usage:
  python scripts/bench_tranco_stratified.py [--per-band 50] [--strategy http]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LIST_FILE = ROOT / "data" / "tranco" / "top-10k.txt"
OUT_DIR = ROOT / "data" / "tranco" / "results_2026-09-18"

PER_URL_TIMEOUT = 8
CONCURRENCY = 40
GROUP_SIZE = 100
MAX_CHARS = 2000
BANDS = [(0, 2000), (2000, 4000), (4000, 6000), (6000, 8000), (8000, 10000)]


def load_domains():
    domains = []
    with open(LIST_FILE, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(",")
            domains.append(parts[1].strip() if len(parts) >= 2 else line)
    return domains


def stratified_sample(domains, per_band):
    """Even sample across each 2000-rank band (the old SUMMARY's key axis).

    Rank must be the GLOBAL index into `domains`, not the index inside the
    band slice, otherwise every band reports itself as rank 1..N and the
    per-band analysis silently collapses into one band.

    Sampling is deterministic (fixed stride from the band start) so a re-run
    compares the same domains.
    """
    picked = []
    for lo, hi in BANDS:
        band = domains[lo:hi]
        step = max(1, len(band) // per_band)
        idxs = list(range(0, len(band), step))[:per_band]
        picked.extend((lo + i, band[i]) for i in idxs)
    return picked


async def run_group(ranked_urls, strategy):
    import webget_cli as wg

    urls = [u for _, u in ranked_urls]
    t0 = time.perf_counter()
    try:
        res = await asyncio.wait_for(
            wg.scrape_many(
                urls,
                max_chars=MAX_CHARS,
                per_url_timeout=PER_URL_TIMEOUT,
                no_cache=False,
                max_concurrency=CONCURRENCY,
                strategy=strategy,
            ),
            timeout=GROUP_SIZE * PER_URL_TIMEOUT,
        )
    except (TimeoutError, Exception) as e:  # noqa: BLE001
        print(f"!! group failed: {type(e).__name__}: {e}", flush=True)
        res = {}
    return time.perf_counter() - t0, res


async def main_async(args):
    import webget_cli as wg

    domains = load_domains()
    assert len(domains) == 10000, f"expected 10000, got {len(domains)}"
    sample = stratified_sample(domains, args.per_band)
    print(f"stratified sample: {len(sample)} domains across {len(BANDS)} bands", flush=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # Isolate the cache. The override hook is the shim module attribute
    # (webget/cache.py::_cache_dir), NOT a scrape_many kwarg. Without this
    # the run would read/write the September run's shared ~/.cache/webget
    # and cache hits would silently inflate the success rate.
    cache_dir = tempfile.mkdtemp(prefix="webget-bench-cache-")
    wg.CACHE_DIR = cache_dir
    print(f"cache dir (isolated): {cache_dir}", flush=True)

    out_path = OUT_DIR / f"stratified_{args.per_band * len(BANDS)}.jsonl"
    rows = []
    groups = [sample[i : i + GROUP_SIZE] for i in range(0, len(sample), GROUP_SIZE)]
    for gi, grp in enumerate(groups):
        ranked = [(r, f"https://{d}/") for r, d in grp]
        elapsed, res = await run_group(ranked, args.strategy)
        for r, url in ranked:
            got = res.get(url, {})
            rows.append(
                {
                    "rank": r + 1,
                    "domain": url.removeprefix("https://").removesuffix("/"),
                    "status": got.get("status", "error") if got else "error",
                    "method": got.get("method", ""),
                    "cached": got.get("cached", False),
                    "attempts": got.get("attempts", 0),
                    "error": got.get("error"),
                    "markdown_len": len(got.get("markdown") or ""),
                }
            )
        print(
            f"  group {gi + 1}/{len(groups)} | {elapsed:.0f}s | "
            f"succ={sum(1 for x in rows if x['status'] == 'success')}",
            flush=True,
        )

    with open(out_path, "w", encoding="utf-8") as f:  # noqa: ASYNC230
        f.writelines(json.dumps(row) + "\n" for row in rows)
    print(f"wrote {out_path} ({len(rows)} rows)", flush=True)
    return out_path, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-band", type=int, default=50)
    ap.add_argument("--strategy", default="http")
    args = ap.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
