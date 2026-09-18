"""Benchmark the ddgs search engines that webget can now select via --engine.

What this measures, and what it does NOT:

  MEASURES (objective, reproducible here):
    - reachability: does the engine return results at all (DDG upstream was
      returning "No results found" during development, so this matters)
    - latency: wall-clock per call, single-engine vs the auto metasearch
    - result count returned for a fixed max_results
    - overlap between engines on the same query (Jaccard on result domains)
    - top-1 domain correctness against a small hand-written ground truth

  DOES NOT MEASURE (would need paid APIs / is not reproducible here):
    - semantic ranking quality vs Tavily/Exa. We have no Tavily/Exa keys and
      inventing a comparison against them would be fabrication. See the KB
      note in topics/webget-kompetitor-landscape.md. The ground-truth set
      below is deliberately tiny and honest about being a smoke signal, not
      a ranking benchmark.

Usage:
  python scripts/bench_search_engines.py [--repeats 3] [--max-results 5]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT_DIR = ROOT / "data" / "search_bench"

# Queries with an unambiguous "the right answer is this domain" target, so a
# top-1 hit is a real signal rather than a taste judgement. Kept small and
# obvious on purpose: this is a smoke test, not a ranking benchmark.
GROUND_TRUTH = [
    ("python asyncio documentation", "docs.python.org"),
    ("rust programming language book", "doc.rust-lang.org"),
    ("postgresql official documentation", "postgresql.org"),
    ("wikipedia coffee", "en.wikipedia.org"),
    ("git documentation", "git-scm.com"),
    ("docker get started", "docs.docker.com"),
    ("mdn javascript array", "developer.mozilla.org"),
    ("kubernetes documentation", "kubernetes.io"),
]


def host_of(url):
    try:
        from urllib.parse import urlparse

        h = urlparse(url).hostname or ""
        return h.removeprefix("www.")
    except Exception:  # noqa: BLE001
        return ""


def run_one(engine, query, max_results):
    """One timed search. Returns (ok, latency, domains, error)."""
    from ddgs import DDGS

    t0 = time.perf_counter()
    try:
        kw = {} if engine in (None, "auto") else {"backend": engine}
        res = DDGS().text(query, max_results=max_results, **kw)
        dt = time.perf_counter() - t0
        doms = [host_of(r.get("href") or r.get("url") or "") for r in res]
        return True, dt, doms, None
    except Exception as e:  # noqa: BLE001
        dt = time.perf_counter() - t0
        return False, dt, [], f"{type(e).__name__}: {e}"


def jaccard(a, b):
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--max-results", type=int, default=5)
    args = ap.parse_args()

    from webget.search import known_engines

    engines = ["auto", *known_engines()]
    print(f"engines: {engines}", flush=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    per_engine = {
        e: {
            "ok": 0,
            "fail": 0,
            "lat": [],
            "results": [],
            "top1_hit": 0,
            "errors": [],
            "domain_sets": [],
        }
        for e in engines
    }

    for qi, (query, target) in enumerate(GROUND_TRUTH, 1):
        print(f"\n[{qi}/{len(GROUND_TRUTH)}] {query!r} (target: {target})", flush=True)
        for eng in engines:
            best_doms, lat_samples, ok_any = [], [], False
            for _ in range(args.repeats):
                ok, dt, doms, err = run_one(eng, query, args.max_results)
                lat_samples.append(dt)
                if ok:
                    ok_any = True
                    if doms:
                        best_doms = doms
                elif err:
                    per_engine[eng]["errors"].append(err)
            st = per_engine[eng]
            if ok_any:
                st["ok"] += 1
            else:
                st["fail"] += 1
            st["lat"].append(statistics.median(lat_samples))
            st["results"].append(len(best_doms))
            st["domain_sets"].append(best_doms)
            if best_doms and best_doms[0] == target:
                st["top1_hit"] += 1
            print(
                f"    {eng:12s} ok={ok_any!s:5s} med={statistics.median(lat_samples):5.2f}s "
                f"n={len(best_doms)} top1={'HIT' if best_doms and best_doms[0] == target else '-'}",
                flush=True,
            )

    # Summary
    n_q = len(GROUND_TRUTH)
    summary = []
    for eng in engines:
        st = per_engine[eng]
        med = statistics.median(st["lat"]) if st["lat"] else 0.0
        summ = {
            "engine": eng,
            "success_rate": round(st["ok"] / n_q, 3),
            "median_latency_s": round(med, 2),
            "avg_results": round(sum(st["results"]) / n_q, 1),
            "top1_accuracy": round(st["top1_hit"] / n_q, 3),
            "sample_errors": st["errors"][:3],
        }
        summary.append(summ)
    summary.sort(key=lambda s: (-s["top1_accuracy"], s["median_latency_s"]))

    print("\n=== SUMMARY (sorted by top1 accuracy, then speed) ===")
    print(f"{'engine':12s} {'succ':>5s} {'med_lat':>8s} {'avg_n':>6s} {'top1':>6s}")
    for s in summary:
        print(
            f"{s['engine']:12s} {s['success_rate']:5.2f} {s['median_latency_s']:7.2f}s "
            f"{s['avg_results']:6.1f} {s['top1_accuracy']:6.2f}"
        )

    # Pairwise overlap on the shared query set (only for engines that ran)
    print("\n=== OVERLAP between auto and each single engine (mean Jaccard on domains) ===")
    auto_sets = per_engine["auto"]["domain_sets"]
    overlaps = {}
    for eng in engines:
        if eng == "auto":
            continue
        sets = per_engine[eng]["domain_sets"]
        vals = [jaccard(a, b) for a, b in zip(auto_sets, sets)]
        overlaps[eng] = round(statistics.mean(vals), 3) if vals else 0.0
        print(f"  auto vs {eng:12s} {overlaps[eng]:.3f}")

    out = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "queries": [q for q, _ in GROUND_TRUTH],
        "repeats": args.repeats,
        "max_results": args.max_results,
        "summary": summary,
        "overlap_vs_auto": overlaps,
        "caveat": (
            "top1_accuracy is against 8 obvious queries; it is a smoke signal, "
            "NOT a semantic ranking benchmark. No Tavily/Exa comparison: no keys."
        ),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "engines.json"
    path.write_text(json.dumps(out, indent=2))
    print(f"\nwrote {path}", flush=True)


if __name__ == "__main__":
    main()
