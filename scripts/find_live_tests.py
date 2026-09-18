#!/usr/bin/env python3
"""Find the tests that genuinely reach the public internet.

The per-file classifier in classify_tests.py runs with sockets disabled but
localhost allowed, which is right for the non-MCP suite. The MCP suite needs a
second pass because it cannot even be collected without fastmcp.

Method: run the MCP files twice, once normally and once with all sockets
blocked (no localhost exception either, since these may talk to either). Any
test that passes normally and errors when blocked is hitting the network. This
is an experiment, not a filename heuristic.

Usage: python scripts/find_live_tests.py [--json out.json]
"""

import json
import re
import subprocess
from pathlib import Path

ROOT = Path("/home/hermawan/oss/webget")
PY = "/tmp/realcount/bin/python" if Path("/tmp/realcount/bin/python").exists() else str(
    ROOT / ".venv/bin/python"
)

MCP_FILES = [
    "test_mcp_server", "test_mcp_smoke", "test_adversarial_mcp",
    "test_mcp_leak_review", "test_mcp_profile", "test_login_flow",
    "test_mcp_map", "test_mcp_metadata", "test_mcp_engine",
    "test_mcp_provenance",
]


def run(files, block):
    args = [PY, "-m", "pytest", *files, "-p", "no:cacheprovider",
            "--timeout=180", "-v", "-rf"]
    if block:
        args += ["--disable-socket"]
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=1500)


def outcomes(stdout):
    """Map 'path::test' -> PASSED/FAILED/ERROR from -v output."""
    res = {}
    for m in re.finditer(r"^(tests/\S+::\S+)\s+(PASSED|FAILED|ERROR|SKIPPED)", stdout, re.MULTILINE):
        res[m.group(1)] = m.group(2)
    return res


files = [f"tests/{m}.py" for m in MCP_FILES if (ROOT / f"tests/{m}.py").exists()]
print(f"running {len(files)} MCP files, online first...", flush=True)
online = run(files, block=False)
print(f"  {online.stdout.strip().splitlines()[-1] if online.stdout.strip() else 'no output'}", flush=True)

print("running again with sockets blocked...", flush=True)
offline = run(files, block=True)

on, off = outcomes(online.stdout), outcomes(offline.stdout)

only_online = sorted(k for k, v in on.items()
                     if v == "PASSED" and off.get(k) in (None, "FAILED", "ERROR"))
live = [k for k in only_online]

print(f"\ntests passing online but not offline ({len(live)}):")
for t in live:
    print(f"  {t}")

out = {
    "online_summary": online.stdout.strip().splitlines()[-1] if online.stdout.strip() else "",
    "offline_summary": offline.stdout.strip().splitlines()[-1] if offline.stdout.strip() else "",
    "live_network_tests": live,
    "total_online": len(on),
}
Path("/tmp/live_tests.json").write_text(json.dumps(out, indent=2))
print("\nwrote /tmp/live_tests.json")
