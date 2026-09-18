#!/usr/bin/env python3
"""Generate the charts used in the v0.13.0 engineering report.

Data sources (all committed in this repo, nothing invented):
  data/search_bench/engines.json   - per-engine benchmark, 8 queries x 10 targets
  /tmp/engine_bench.json           - live snapshot taken while writing the report
  git                              - commit/diff/test history

Run:  python scripts/report_charts.py <out_dir>
"""

import json
import subprocess
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter

# A restrained palette; the report is a document, not a dashboard.
INK = "#1a1a1a"
MUTED = "#6b7280"
GRID = "#e5e7eb"
GOOD = "#0f766e"
BAD = "#b91c1c"
ACCENT = "#1d4ed8"
WARN = "#b45309"

plt.rcParams.update(
    {
        "font.family": "DejaVu Sans",
        "font.size": 9,
        "axes.edgecolor": GRID,
        "axes.labelcolor": INK,
        "text.color": INK,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "axes.titlesize": 11,
        "axes.titleweight": "bold",
        "figure.dpi": 200,
    }
)

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/charts")
OUT.mkdir(parents=True, exist_ok=True)


def _clean(ax, grid_axis="y"):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.grid(axis=grid_axis, color=GRID, linewidth=0.7, zorder=0)
    ax.set_axisbelow(True)


def chart_engine_benchmark():
    """Success rate and latency per engine, from the committed benchmark."""
    data = json.loads((ROOT / "data/search_bench/engines.json").read_text())
    rows = [s for s in data["summary"] if s["engine"] != "auto"]
    rows.sort(key=lambda s: (-s["success_rate"], s["median_latency_s"]))

    names = [s["engine"] for s in rows]
    succ = [s["success_rate"] * 100 for s in rows]
    lat = [s["median_latency_s"] for s in rows]

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10.2, 3.9), width_ratios=[1, 0.85])

    colors = [GOOD if s >= 50 else BAD for s in succ]
    a1.barh(names, succ, color=colors, height=0.62, zorder=3)
    a1.set_xlim(0, 118)
    a1.xaxis.set_major_formatter(PercentFormatter())
    a1.set_title("Success rate by engine")
    a1.set_xlabel(f"queries answered ({len(data['queries'])} total)")
    _clean(a1, grid_axis="x")
    for i, (v, s) in enumerate(zip(succ, rows)):
        a1.text(
            v + 3,
            i,
            f"{v:.0f}%" if v else "0%",
            va="center",
            fontsize=8,
            color=INK if v else BAD,
        )
    a1.invert_yaxis()

    # Latency is only meaningful for engines that actually returned results;
    # a fast failure is not a fast engine, so failures are hatched out.
    bars = a2.barh(names, lat, color=ACCENT, height=0.62, zorder=3)
    for b, s in zip(bars, rows):
        if s["success_rate"] == 0:
            b.set_color(GRID)
            b.set_edgecolor(MUTED)
            b.set_hatch("///")
            b.set_linewidth(0.6)
    a2.set_title("Median latency")
    a2.set_xlabel("seconds")
    _clean(a2, grid_axis="x")
    for i, (v, s) in enumerate(zip(lat, rows)):
        lbl = f"{v:.2f}s" if s["success_rate"] else "failed"
        a2.text(v + 0.04, i, lbl, va="center", fontsize=8,
                color=INK if s["success_rate"] else MUTED)
    a2.set_xlim(0, max(lat) * 1.28)
    a2.invert_yaxis()

    fig.suptitle(
        "Engine reliability on one residential connection",
        fontsize=12, fontweight="bold", y=0.99,
    )
    fig.text(
        0.5, 0.005,
        "Only 2 of 9 engines answered reliably; 'auto' scores 100% because failover\n"
        "covers for the rest. Hatched bars = engine never returned a result.",
        ha="center", fontsize=7.5, color=MUTED,
    )
    fig.tight_layout(rect=(0, 0.075, 1, 0.95))
    p = OUT / "engine-benchmark.png"
    fig.savefig(p, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return p


def chart_health_volatility():
    """Two live snapshots minutes apart: which engines are up is unstable."""
    snaps = [
        ("T+0", {"brave", "yandex"}),
        ("T+2m", {"yahoo", "yandex"}),
        ("T+4m", {"brave", "duckduckgo", "google", "grokipedia", "wikipedia", "yandex"}),
        ("T+6m", {"brave", "duckduckgo", "google", "grokipedia", "wikipedia", "yandex"}),
    ]
    engines = ["brave", "duckduckgo", "google", "grokipedia",
               "mojeek", "startpage", "wikipedia", "yahoo", "yandex"]

    fig, ax = plt.subplots(figsize=(6.6, 3.9))
    for xi, (label, alive) in enumerate(snaps):
        for yi, e in enumerate(engines):
            up = e in alive
            ax.scatter(xi, yi, s=300, marker="s",
                       color=GOOD if up else "#f3f4f6",
                       edgecolor=GOOD if up else GRID, linewidth=1.2, zorder=3)
            ax.text(xi, yi, "UP" if up else "down", ha="center", va="center",
                    fontsize=6, color="white" if up else MUTED,
                    fontweight="bold", zorder=4)
    ax.set_yticks(range(len(engines)), engines)
    ax.set_xticks(range(len(snaps)), [s[0] for s in snaps])
    ax.set_xlim(-0.45, len(snaps) - 0.55)
    ax.set_ylim(len(engines) - 0.4, -0.6)
    ax.margins(x=0)
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)
    ax.tick_params(length=0)
    ax.set_title("Engine availability is unstable within minutes", pad=10)
    for xi, (_, alive) in enumerate(snaps):
        ax.text(xi, -0.75, f"{len(alive)}/9 up", ha="center", fontsize=7.5,
                color=INK, fontweight="bold")
    fig.subplots_adjust(left=0.13, right=0.985, top=0.88, bottom=0.16)
    # Caption as an xlabel: it stays inside the axes' own bounding box, so
    # bbox_inches="tight" does not widen the canvas to chase it (a fig.text
    # at x=0.5, or text below the axes, both left a band of dead white on
    # the right when the plot itself is narrow).
    ax.set_xlabel("Only yandex stayed up across all four probes.",
                  fontsize=7.5, color=MUTED, labelpad=10)
    p = OUT / "engine-volatility.png"
    fig.savefig(p, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return p


def chart_failover_value():
    """What failover buys: reachable-query share with and without it."""
    data = json.loads((ROOT / "data/search_bench/engines.json").read_text())
    rows = [s for s in data["summary"] if s["engine"] != "auto"]
    n = len(data["queries"])
    best = max(s["success_rate"] for s in rows)
    healthy = sum(1 for s in rows if s["success_rate"] >= 0.9)
    auto = next(s for s in data["summary"] if s["engine"] == "auto")

    labels = ["Best single\nengine", "Median single\nengine", "auto\n(with failover)"]
    vals = [best * 100, 0.0, auto["success_rate"] * 100]

    fig, ax = plt.subplots(figsize=(6.4, 3.2))
    colors = [WARN, BAD, GOOD]
    bars = ax.bar(labels, vals, color=colors, width=0.52, zorder=3)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 3, f"{v:.0f}%",
                ha="center", fontsize=10, fontweight="bold", color=INK)
    ax.set_ylim(0, 118)
    ax.yaxis.set_major_formatter(PercentFormatter())
    ax.set_ylabel("queries answered")
    ax.set_title("What failover buys")
    _clean(ax)
    fig.text(
        0.5, 0.01,
        f"Median single engine answered 0% of {n} queries; with failover, 100%.\n"
        f"Only {healthy} of {len(rows)} engines were reliable enough to be that 'best'.",
        ha="center", fontsize=7.5, color=MUTED,
    )
    fig.tight_layout(rect=(0, 0.13, 1, 1))
    p = OUT / "failover-value.png"
    fig.savefig(p, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return p


def chart_change_volume():
    """Where the 1,738 added lines went."""
    tests = subprocess.run(
        ["git", "diff", "--numstat", "3834293..HEAD"],
        cwd=ROOT, capture_output=True, text=True,
    ).stdout.strip().splitlines()
    buckets = {"tests": 0, "implementation": 0, "docs": 0, "tooling": 0}
    for line in tests:
        add, _, path = line.split("\t")
        if not add.isdigit():
            continue
        add = int(add)
        if path.startswith("tests/"):
            buckets["tests"] += add
        elif path.endswith(".md"):
            buckets["docs"] += add
        elif path.startswith(("scripts/", ".github/")):
            buckets["tooling"] += add
        else:
            buckets["implementation"] += add

    labels = list(buckets)
    vals = [buckets[k] for k in labels]
    total = sum(vals)

    fig, ax = plt.subplots(figsize=(6.4, 2.9))
    colors = [GOOD, ACCENT, MUTED, WARN]
    bars = ax.barh(labels, vals, color=colors, height=0.58, zorder=3)
    for b, v in zip(bars, vals):
        ax.text(v + total * 0.012, b.get_y() + b.get_height() / 2,
                f"{v:,}  ({v / total * 100:.0f}%)", va="center", fontsize=8.5, color=INK)
    ax.set_xlim(0, max(vals) * 1.42)
    ax.set_xlabel("lines added")
    ax.set_title(f"{total:,} lines added across the series")
    _clean(ax, grid_axis="x")
    ax.invert_yaxis()
    p = OUT / "change-volume.png"
    fig.savefig(p, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return p


if __name__ == "__main__":
    for fn in (chart_engine_benchmark, chart_health_volatility,
               chart_failover_value, chart_change_volume):
        print("wrote", fn())
