"""Analyse the stratified re-benchmark against the 2026-09-01 Tranco run.

Reports per-band success so the comparison is band-for-band against the old
SUMMARY's rank->reachability curve, not just a single aggregate (the old run's
aggregate was dominated by the 1-2000 band being over-represented in network
terms). Also flags explicitly that this is a 250-domain sample, not 10,000.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NEW = ROOT / "data" / "tranco" / "results_2026-09-18" / "stratified_250.jsonl"
OLD_DIR = ROOT / "data" / "tranco" / "results"

# From data/tranco/SUMMARY.md (2026-09-01, full 10k run)
OLD_BANDS = {  # band -> (n, successes)
    "1-2000": (2000, 0.313),
    "2001-4000": (2000, None),
    "4001-6000": (2000, None),
    "6001-8000": (2000, None),
    "8001-10000": (2000, 0.180),
}

BANDS = [(1, 2000), (2001, 4000), (4001, 6000), (6001, 8000), (8001, 10000)]


def old_per_band():
    """Compute the OLD run's per-band success from its raw jsonl files."""
    rows = []
    for p in sorted(OLD_DIR.glob("batch_*.jsonl")):
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    if not rows:
        return {}
    # The old files are in Tranco rank order per batch (1000 per batch).
    out = {}
    for lo, hi in BANDS:
        # global index = batch*1000 + position; reconstruct by order
        pass
    # Simpler: the old run wrote batches sequentially in rank order, so the
    # concatenated order IS rank order.
    for bi, (lo, hi) in enumerate(BANDS):
        chunk = rows[(bi * 2000) : ((bi + 1) * 2000)]
        succ = sum(1 for r in chunk if r.get("status") == "success")
        out[f"{lo}-{hi}"] = (len(chunk), succ, succ / len(chunk) if chunk else 0)
    return out


def main():
    with open(NEW, encoding="utf-8") as f:
        new = [json.loads(line) for line in f if line.strip()]

    print(f"NEW sample: {len(new)} domains (stratified, cache isolated, http)\n")

    print("=== NEW: per-band ===")
    print(f"{'band':12s} {'n':>4s} {'succ':>5s} {'rate':>7s}")
    new_band = {}
    for lo, hi in BANDS:
        chunk = [r for r in new if lo <= r["rank"] <= hi]
        succ = sum(1 for r in chunk if r["status"] == "success")
        rate = succ / len(chunk) if chunk else 0
        new_band[f"{lo}-{hi}"] = (len(chunk), succ, rate)
        print(f"{f'{lo}-{hi}':12s} {len(chunk):4d} {succ:5d} {rate:7.1%}")

    print("\n=== OLD (2026-09-01, full 10k) vs NEW (250 sample), per band ===")
    oldb = old_per_band()
    print(f"{'band':12s} {'old_rate':>9s} {'new_rate':>9s} {'delta':>8s}")
    for k in new_band:
        o = oldb.get(k)
        n = new_band[k][2]
        if o:
            print(f"{k:12s} {o[2]:9.1%} {n:9.1%} {n - o[2]:+8.1%}")
        else:
            print(f"{k:12s} {'n/a':>9s} {n:9.1%} {'':>8s}")

    old_total = (10000, 2030, 0.203)
    new_total_succ = sum(1 for r in new if r["status"] == "success")
    new_rate = new_total_succ / len(new)
    print(
        f"\nTOTAL: old {old_total[2]:.1%} (10,000) | "
        f"new {new_rate:.1%} ({len(new)}) | delta {new_rate - old_total[2]:+.1%}"
    )

    print("\n=== NEW: status breakdown ===")
    for status, c in Counter(r["status"] for r in new).most_common():
        print(f"  {status:16s} {c:4d}  {c / len(new):6.1%}")

    print("\n=== NEW: method breakdown ===")
    for m, c in Counter(r["method"] or "(none)" for r in new).most_common():
        print(f"  {m:16s} {c:4d}  {c / len(new):6.1%}")

    print("\n=== NEW: top error strings ===")
    errs = Counter((r.get("error") or "")[:60] for r in new if r["status"] != "success")
    for e, c in errs.most_common(10):
        print(f"  {c:4d}  {e or '(empty)'}")

    nz = [r["markdown_len"] for r in new if r["status"] == "success"]
    if nz:
        nz.sort()
        print(
            f"\nmarkdown_len (success): min={nz[0]} median={nz[len(nz) // 2]} "
            f"max={nz[-1]} (old avg was 1,683)"
        )


if __name__ == "__main__":
    main()
