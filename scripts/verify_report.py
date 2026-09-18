#!/usr/bin/env python3
"""Verify the report's factual claims against the repository and PyPI.

A report that overstates results is worse than no report, so every number in
the PDF is re-derived from the same sources the report cites. This exists
because the first draft claimed 423 tests - a number copied from a commit
message - while the suite actually contains 416.

The suite cannot be run in one pass: MCP test files import webget_mcp, which
exits when fastmcp is missing. CI splits it into two jobs, and so does this.
"""

import json
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path("/home/hermawan/oss/webget")
PY = str(ROOT / ".venv/bin/python")
PDF = "/tmp/webget-0.13.0-report.pdf"

PDF_TEXT = subprocess.run(
    ["pdftotext", "-layout", PDF, "-"], capture_output=True, text=True
).stdout

checks = []


def check(label, claimed, actual, ok=None):
    if ok is None:
        ok = str(claimed).strip() == str(actual).strip()
    checks.append((label, claimed, actual, ok))


MCP_FILES = [
    "test_mcp_server", "test_mcp_smoke", "test_adversarial_mcp",
    "test_mcp_leak_review", "test_mcp_profile", "test_login_flow",
    "test_mcp_map", "test_mcp_metadata", "test_mcp_engine",
    "test_mcp_provenance",
]
# test_browser_ssrf needs playwright and is scoped out of the `test` job.
IGNORE_EXTRA = ["test_browser_ssrf"]


# The authoritative source is CI, which runs the suite in a venv that has the
# MCP extras. Counting is done against that interpreter when available, because
# a bare venv (no fastmcp) collects a different MCP total: test_mcp_metadata
# imports webget_mcp lazily, so without fastmcp four of its cases are not
# collected. Pinning to the wrong interpreter silently shifts the number, which
# is how an earlier draft of this report ended up claiming 423.
PY_FULL = "/tmp/realcount/bin/python" if Path("/tmp/realcount/bin/python").exists() else PY


def collect_count(ignore, python=PY_FULL):
    args = [f"--ignore=tests/{f}.py" for f in ignore]
    r = subprocess.run(
        [python, "-m", "pytest", "tests/", "-q", "--collect-only",
         "-p", "no:cacheprovider", *args],
        cwd=ROOT, capture_output=True, text=True,
    )
    return sum(int(n) for n in re.findall(r"^tests/\S+\.py: (\d+)$", r.stdout, re.MULTILINE))


def mcp_count():
    files = [f"tests/{m}.py" for m in MCP_FILES if (ROOT / f"tests/{m}.py").exists()]
    r = subprocess.run(
        [PY_FULL, "-m", "pytest", *files, "-q", "--collect-only",
         "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
    )
    return sum(int(x) for x in re.findall(r"^tests/\S+\.py: (\d+)$", r.stdout, re.MULTILINE))


total = collect_count(MCP_FILES + IGNORE_EXTRA) + mcp_count()

check("total tests", "416", total)

# New tests added by this series.
new = sum(
    len(re.findall(r"def test_", (ROOT / f"tests/{f}.py").read_text()))
    for f in ["test_search_engine", "test_search_failover", "test_search_provenance",
              "test_cli_engine", "test_cli_provenance", "test_mcp_engine",
              "test_mcp_provenance"]
    if (ROOT / f"tests/{f}.py").exists()
)
check("new tests", "50", new)

bench = json.loads((ROOT / "data/search_bench/engines.json").read_text())
rows = [s for s in bench["summary"] if s["engine"] != "auto"]
check("reliable engines", "2", sum(1 for s in rows if s["success_rate"] >= 0.9))
check("engines benchmarked", "9", len(rows))
check("queries per engine", "8", len(bench["queries"]))

head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                      capture_output=True, text=True).stdout.strip()
check("HEAD SHA in PDF", "yes", "yes" if head in PDF_TEXT else "no")

try:
    with urllib.request.urlopen(
        "https://pypi.org/pypi/webget-cli/0.13.0/json", timeout=15
    ) as r:
        code = r.status
except urllib.error.HTTPError as ex:
    code = ex.code
except Exception as ex:  # noqa: BLE001
    code = f"err:{type(ex).__name__}"
check("PyPI 0.13.0 status", "404", code)

ds = subprocess.run(["git", "diff", "--shortstat", "3834293..HEAD"], cwd=ROOT,
                    capture_output=True, text=True).stdout.strip()
check("diffstat shows 1738 insertions", "yes",
      "yes" if "1738 insertions" in ds else "no")

check("em-dash count in PDF", "0", PDF_TEXT.count("\u2014"))

# The PDF must not repeat the bad figure or contain unresolved template braces.
check("PDF free of stale '423'", "no",
      "yes" if re.search(r"Tests\s+423", PDF_TEXT) else "no",
      ok=not re.search(r"Tests\s+423", PDF_TEXT))
check("PDF free of raw template braces", "no",
      "yes" if re.search(r"\{[a-z_]+\}", PDF_TEXT) else "no",
      ok=not re.search(r"\{[a-z_]+\}", PDF_TEXT))

print(f"{'CHECK':34s} {'CLAIMED':>10s} {'ACTUAL':>34s}")
print("-" * 84)
bad = 0
for label, claimed, actual, ok in checks:
    if not ok:
        bad += 1
    print(f"{label:34s} {claimed!s:>10s} {actual!s:>34s}  {'OK' if ok else 'FAIL'}")
print("-" * 84)
print(f"{len(checks) - bad}/{len(checks)} claims verified"
      + ("" if not bad else f"  ({bad} FAILED)"))
