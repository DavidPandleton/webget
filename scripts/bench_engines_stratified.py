"""Stratified engine benchmark: many more queries than the original 8.

The 0.13.0 benchmark used 8 hand-picked documentation queries with an obvious
correct domain. That is enough to answer "is this engine reachable" and nothing
more; it cannot support a reliability or quality claim. This script widens the
query set along two axes so those claims have something behind them:

  STRATA                why it is a distinct stratum
  --------------------------------------------------------------------------
  docs          navigational, unambiguous target (the original queries)
  code          programming-specific, many plausible hosts
  news          time-sensitive; a correct answer is not a single domain
  commerce      product intent; dominated by a few large hosts
  science       academic; pulls .edu/.org and preprints
  local         place intent; strongly geo-dependent, expected to vary
  tranco-head   queries built from high-rank Tranco domains (top 1k)
  tranco-tail   queries built from lower-rank Tranco domains (10k..100k)

Every stratum is reported separately. An aggregate number over a mixed set
would hide the most interesting result, which is that engine reliability is
not uniform across query types.

Ground truth is only defined for the `docs` stratum, where "the answer is this
domain" is objective. For other strata we measure reachability, result count
and cross-engine agreement (Jaccard on hosts), not ranking accuracy. Reporting
a single "accuracy" number across all strata would be fabrication.

Usage:
  python scripts/bench_engines_stratified.py --per-stratum 25 --repeats 2
  python scripts/bench_engines_stratified.py --quick      # 5/stratum, 1 repeat
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "data" / "search_bench"
TRANCO_1M = ROOT / "data" / "tranco" / "top-1m.csv"

# Templates that turn a bare domain into a plausible navigational query. The
# point is not linguistic quality; it is to exercise real hosts rather than
# seeds the author chose because they were known to work.
DOC_TEMPLATES = ["{d} documentation", "{d} official site", "{d} api reference"]

# --- strata with fixed, hand-written queries -------------------------------
# Kept short and honest. These are seeds, not a curated benchmark; the width
# comes from --per-stratum, which samples from them.

STRATA: dict[str, list[tuple[str, str | None]]] = {
    # (query, expected_domain or None when no objective answer exists)
    "docs": [
        ("python asyncio documentation", "docs.python.org"),
        ("rust programming language book", "doc.rust-lang.org"),
        ("postgresql official documentation", "postgresql.org"),
        ("git documentation", "git-scm.com"),
        ("docker get started", "docs.docker.com"),
        ("mdn javascript array", "developer.mozilla.org"),
        ("kubernetes documentation", "kubernetes.io"),
        ("nginx configuration guide", "nginx.org"),
        ("redis command reference", "redis.io"),
        ("sqlite pragma documentation", "sqlite.org"),
        ("golang net http package", "pkg.go.dev"),
        ("linux man pages online", "man7.org"),
    ],
    "code": [
        ("how to debounce in javascript", None),
        ("python dataclass vs pydantic", None),
        ("rust lifetime elision rules", None),
        ("postgres index only scan example", None),
        ("git rebase interactive squash", None),
        ("docker compose healthcheck syntax", None),
        ("react useEffect cleanup async", None),
        ("bash set -euo pipefail semantics", None),
        ("sql window function partition by", None),
        ("python asyncio gather exception handling", None),
    ],
    "news": [
        ("september 2026 technology announcements", None),
        ("semiconductor export controls latest", None),
        ("open source license change controversy", None),
        ("data breach disclosure this week", None),
        ("ai regulation bill status", None),
    ],
    "commerce": [
        ("best mechanical keyboard 75 percent", None),
        ("usb c dock recommendations", None),
        ("raspberry pi 5 power supply specs", None),
        ("ssd 2tb nvme price comparison", None),
        ("noise cancelling headphones review", None),
    ],
    "science": [
        ("arxiv transformer scaling laws", None),
        ("protein folding benchmark dataset", None),
        ("crispr off target detection methods", None),
        ("neutrino oscillation best fit parameters", None),
        ("climate model cmip6 downscaling", None),
    ],
    "local": [
        ("coffee shop near ubaya surabaya", None),
        ("apotek 24 jam surabaya", None),
        ("bengkel motor terdekat surabaya", None),
        ("kos kosan murah rungkut", None),
        ("laundry kiloan surabaya timur", None),
    ],
}


def host_of(url):
    try:
        h = urlparse(url or "").hostname or ""
        return h.removeprefix("www.")
    except Exception:  # noqa: BLE001
        return ""


def load_tranco_queries(per_stratum, seed=20260919):
    """Build two more strata from the real Tranco list.

    Head = top 1k, tail = 10k..100k. Sampling from the tail matters: those are
    the hosts an engine is least likely to have indexed well, so they separate
    engines that genuinely crawl from engines that lean on a popularity prior.
    """
    if not TRANCO_1M.exists():
        return {}, {}
    rng = random.Random(seed)
    head, tail = [], []
    with TRANCO_1M.open(errors="ignore") as fh:
        for line in fh:
            rank, _, dom = line.strip().partition(",")
            if not dom or not rank.isdigit():
                continue
            r = int(rank)
            if r <= 1000 and len(head) < per_stratum * 8:
                head.append(dom)
            elif 10_000 <= r <= 100_000 and len(tail) < per_stratum * 8:
                tail.append(dom)
            if len(head) >= per_stratum * 8 and len(tail) >= per_stratum * 8:
                break

    def build(domains, n):
        out = []
        for d in rng.sample(domains, min(n, len(domains))):
            tpl = rng.choice(DOC_TEMPLATES)
            out.append((tpl.format(d=d), d))
        return out

    return {"tranco-head": build(head, per_stratum)}, {"tranco-tail": build(tail, per_stratum)}


def run_one(engine, query, max_results):
    from ddgs import DDGS

    t0 = time.perf_counter()
    try:
        kw = {} if engine in (None, "auto") else {"backend": engine}
        res = DDGS().text(query, max_results=max_results, **kw)
        dt = time.perf_counter() - t0
        doms = [host_of(r.get("href") or r.get("url") or "") for r in res]
        return True, dt, doms, None
    except Exception as e:  # noqa: BLE001
        return False, time.perf_counter() - t0, [], f"{type(e).__name__}: {e}"


def jaccard(a, b):
    sa, sb = set(filter(None, a)), set(filter(None, b))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-stratum", type=int, default=25)
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--max-results", type=int, default=5)
    ap.add_argument("--engines", default=None,
                    help="comma list; default is auto plus every known engine")
    ap.add_argument("--quick", action="store_true", help="5 per stratum, 1 repeat")
    ap.add_argument("--out", default="engines_stratified.json")
    args = ap.parse_args()

    if args.quick:
        args.per_stratum, args.repeats = 5, 1

    from webget.search import known_engines

    engines = ([*args.engines.split(",")] if args.engines
               else ["auto", *known_engines()])
    print(f"engines ({len(engines)}): {engines}", flush=True)

    strata = {k: list(v) for k, v in STRATA.items()}
    head, tail = load_tranco_queries(args.per_stratum)
    strata.update(head)
    strata.update(tail)

    # Trim each stratum to the requested size; Tranco builder already sized.
    for k in list(strata):
        if k not in ("tranco-head", "tranco-tail"):
            rng = random.Random(hash(k) & 0xFFFF)
            if len(strata[k]) > args.per_stratum:
                strata[k] = rng.sample(strata[k], args.per_stratum)
        print(f"  stratum {k:14s} {len(strata[k])} queries", flush=True)

    per = defaultdict(lambda: {
        "ok": 0, "fail": 0, "lat": [], "n": [], "top1_hit": 0, "gt": 0,
        "errors": [], "host_sets": {},
    })

    total = sum(len(v) for v in strata.values())
    done = 0
    for stratum, queries in strata.items():
        for query, target in queries:
            done += 1
            print(f"[{done}/{total}] {stratum:13s} {query[:52]!r}", flush=True)
            for eng in engines:
                ok_any, lat_s, doms_best = False, [], []
                for _ in range(args.repeats):
                    ok, dt, doms, err = run_one(eng, query, args.max_results)
                    lat_s.append(dt)
                    if ok and doms:
                        ok_any, doms_best = True, doms
                    elif err:
                        per[(stratum, eng)]["errors"].append(err)
                st = per[(stratum, eng)]
                st["ok" if ok_any else "fail"] += 1
                st["lat"].append(statistics.median(lat_s))
                st["n"].append(len(doms_best))
                st["host_sets"][query] = doms_best
                if target:
                    st["gt"] += 1
                    if doms_best and doms_best[0] == target:
                        st["top1_hit"] += 1

    # --- report per stratum -------------------------------------------------
    report = {}
    for stratum, qs in strata.items():
        rows = []
        for eng in engines:
            st = per[(stratum, eng)]
            qn = len(qs)
            rows.append({
                "engine": eng,
                "success_rate": round(st["ok"] / qn, 3),
                "median_latency_s": round(statistics.median(st["lat"]), 2) if st["lat"] else 0.0,
                "avg_results": round(sum(st["n"]) / qn, 1),
                "top1_accuracy": (round(st["top1_hit"] / st["gt"], 3) if st["gt"] else None),
                "sample_errors": st["errors"][:3],
            })
        rows.sort(key=lambda r: (-r["success_rate"], r["median_latency_s"]))
        report[stratum] = rows

    # Cross-engine agreement inside a stratum (do they return the same hosts?)
    agreement = {}
    for stratum in strata:
        auto_sets = per[(stratum, "auto")]["host_sets"]
        row = {}
        for eng in engines:
            if eng == "auto":
                continue
            sets = per[(stratum, eng)]["host_sets"]
            vals = [jaccard(auto_sets.get(q, []), sets.get(q, [])) for q in auto_sets]
            vals = [v for v in vals if v > 0]
            row[eng] = round(statistics.mean(vals), 3) if vals else 0.0
        agreement[stratum] = row

    print("\n=== SUCCESS RATE PER STRATUM ===")
    # Print rows in ENGINES order to match the header. The per-stratum `rows`
    # are sorted best-first for the JSON report; printing them in that order
    # under an unsorted header misaligns every column (this exact bug made the
    # first run's table credit brave with yandex's perfect score).
    hdr = f"{'stratum':14s} " + " ".join(f"{e[:9]:>9s}" for e in engines)
    print(hdr)
    for stratum in strata:
        rows_by_name = {r["engine"]: r for r in report[stratum]}
        cells = " ".join(
            f"{rows_by_name[e]['success_rate']:9.2f}" for e in engines
        )
        print(f"{stratum:14s} {cells}")

    overall = {}
    for eng in engines:
        ok = sum(per[(s, eng)]["ok"] for s in strata)
        overall[eng] = round(ok / total, 3)
    print("\n=== OVERALL SUCCESS (all strata pooled) ===")
    for e, v in sorted(overall.items(), key=lambda kv: -kv[1]):
        print(f"  {e:12s} {v:.3f}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "per_stratum": args.per_stratum,
        "repeats": args.repeats,
        "max_results": args.max_results,
        "engines": engines,
        "strata_sizes": {k: len(v) for k, v in strata.items()},
        "total_queries": total,
        "by_stratum": report,
        "overall_success": overall,
        "agreement_vs_auto": agreement,
        "caveat": (
            "top1_accuracy is defined ONLY for the 'docs' and Tranco strata, "
            "where a correct domain exists. Other strata report reachability "
            "and agreement, not ranking quality. No Tavily/Exa comparison: "
            "those need paid keys."
        ),
    }
    path = OUT_DIR / args.out
    path.write_text(json.dumps(out, indent=2))
    print(f"\nwrote {path}", flush=True)


if __name__ == "__main__":
    main()
