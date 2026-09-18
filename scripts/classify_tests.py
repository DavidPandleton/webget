#!/usr/bin/env python3
"""Classify every test file as offline or network-dependent, by experiment.

Do not classify by filename: a test called test_search_engine may mock
everything, and a test with 'mock' in the name may still reach out. This
disables sockets (pytest-socket) and runs each file; anything that fails to
complete without a socket is network-dependent.

Usage: python scripts/classify_tests.py [--json out.json]
"""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path("/home/hermawan/oss/webget")
PY = str(ROOT / ".venv/bin/python")

MCP_FILES = [
    "test_mcp_server", "test_mcp_smoke", "test_adversarial_mcp",
    "test_mcp_leak_review", "test_mcp_profile", "test_login_flow",
    "test_mcp_map", "test_mcp_metadata", "test_mcp_engine",
    "test_mcp_provenance",
]


def run(path, disable_socket):
    # Note: pyproject sets addopts="-q". Passing another -q would make it -qq,
    # which suppresses the summary line entirely and makes pass counts
    # unreadable. -v overrides it reliably.
    args = [
        PY, "-m", "pytest", str(path), "-p", "no:cacheprovider",
        "--timeout=180", "-v",
    ]
    if disable_socket:
        # pytest-socket registers itself as an entry-point plugin, so naming it
        # with -p raises "Plugin already registered".
        #
        # --allow-hosts is essential: a plain --disable-socket also blocks
        # 127.0.0.1, and most of this suite talks to tests/http_server.py on
        # localhost. Without this the classifier flags deterministic offline
        # tests as network-dependent and would have justified splitting the
        # suite in the wrong place.
        args += ["--disable-socket", "--allow-hosts=127.0.0.1,localhost,::1"]
    r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=600)
    return r


def summarize(out):
    m = re.search(r"(\d+) passed", out)
    passed = int(m.group(1)) if m else 0
    m = re.search(r"(\d+) failed", out)
    failed = int(m.group(1)) if m else 0
    m = re.search(r"(\d+) error", out)
    errors = int(m.group(1)) if m else 0
    blocked = "SocketBlockedError" in out
    return passed, failed, errors, blocked


results = {}
for f in sorted((ROOT / "tests").glob("test_*.py")):
    if f.stem in MCP_FILES:
        # Cannot collect these without fastmcp in the default interpreter.
        results[f.stem] = {"skipped": "mcp file"}
        continue
    try:
        live = run(f, disable_socket=False)
        lp, lf, le, _ = summarize(live.stdout + live.stderr)
        sock = run(f, disable_socket=True)
        sp, sf, se, blocked = summarize(sock.stdout + sock.stderr)
        verdict = "offline"
        if sf or se:
            verdict = "network"
        results[f.stem] = {
            "online_pass": lp, "online_fail": lf,
            "offline_pass": sp, "offline_fail": sf, "offline_error": se,
            "verdict": verdict,
        }
        print(f"{f.stem:34s} online={lp:3d}/{lp + lf + le:<3d} "
              f"offline={sp:3d}/{sp + sf + se:<3d}  {verdict.upper()}")
    except subprocess.TimeoutExpired:
        results[f.stem] = {"verdict": "timeout"}
        print(f"{f.stem:34s} TIMEOUT")

out_path = Path(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[1] == "--json" else Path("/tmp/test_classification.json")
out_path.write_text(json.dumps(results, indent=2))
net = [k for k, v in results.items() if v.get("verdict") == "network"]
print(f"\nnetwork-dependent: {len(net)}")
for n in net:
    print(f"  - {n}")
print(f"wrote {out_path}")
