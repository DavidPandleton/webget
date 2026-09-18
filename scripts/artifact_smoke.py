#!/usr/bin/env python3
"""Smoke-test an INSTALLED webget artifact, as a user would run it.

Why this exists
---------------
Two bugs shipped in 0.13.0 that the full test suite could not catch, because
both lived in the gap between "the tests set the code up" and "a user runs
it":

  1. `failed_over` reported False when every engine failed. The suite built
     provenance through a helper the tests controlled, so the wrong value was
     never observable.
  2. Ruff removed a provenance import (F401) and the CLI silently stopped
     emitting provenance. Every test stayed green because they monkeypatched
     the name back into place.

Neither is reachable from inside the test process. Both are obvious the moment
you install the wheel and run the command. So this script does exactly that:
it imports the INSTALLED package, not the source tree, and asserts on what
comes out of the real CLI.

Design rules
------------
  * Run from a directory that is NOT the repo root, so `import webget` cannot
    resolve to the working copy. Running this in-repo would defeat the point.
  * Never import the test helpers. If a check needs a fixture, it is the wrong
    check for this file.
  * Separate "must always hold" checks from "needs the network" checks. The
    first group gates the build; the second group reports and does not fail a
    release over an ISP.

Usage:
  python scripts/artifact_smoke.py            # offline checks only
  python scripts/artifact_smoke.py --online   # also run live search
Exit code 0 = all gating checks passed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

FAILURES: list[str] = []
PASSES: list[str] = []


def check(name, fn, gating=True):
    try:
        detail = fn()
        PASSES.append(f"{name}: {detail}")
        print(f"  PASS  {name}  {detail}")
    except Exception as e:  # noqa: BLE001
        msg = f"{name}: {type(e).__name__}: {e}"
        if gating:
            FAILURES.append(msg)
            print(f"  FAIL  {msg}")
        else:
            print(f"  WARN  {msg} (non-gating)")


def _in_neutral_cwd():
    """Run in /tmp so imports cannot pick up the repo working copy."""
    return tempfile.mkdtemp(prefix="webget-smoke-")


def import_installed():
    """Import the installed package and prove it is NOT the source tree."""
    import webget

    path = Path(webget.__file__).resolve()
    cwd = Path.cwd().resolve()
    # If this resolves under the repo, the smoke test is meaningless.
    for parent in path.parents:
        if (parent / "pyproject.toml").exists() and parent == cwd:
            raise AssertionError(
                f"imported webget from the working copy ({path}); "
                "run this from outside the repo"
            )
    return str(path)


def _cli():
    """The webget binary from THIS environment, not whatever is on PATH.

    Running `webget` from PATH once tested the author's old uv-tool install
    instead of the artifact under test: the checks passed against code that
    was not the code being shipped. sys.executable/.local pins the check to
    the environment the smoke run is actually in.
    """
    return [sys.executable, "-m", "webget.cli"]


def cli_help():
    r = subprocess.run(_cli() + ["--help"], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise AssertionError(f"--help exited {r.returncode}: {r.stderr[:200]}")
    if "search" not in r.stdout:
        raise AssertionError("--help does not mention 'search'")
    # The engine flag lives on the search subcommand, not the top level, so
    # check it where it actually is.
    r2 = subprocess.run(_cli() + ["s", "--help"], capture_output=True, text=True, timeout=60)
    if r2.returncode != 0:
        raise AssertionError(f"'webget s --help' exited {r2.returncode}")
    if "engine" not in r2.stdout:
        raise AssertionError("'webget s --help' does not mention 'engine'")
    return "top level + search subcommand ok"


def known_engines_present():
    mod = _search_module()
    engines = mod.known_engines()
    if not engines:
        raise AssertionError("known_engines() is empty")
    return f"{len(engines)} engines"


def _search_module():
    """Return the webget.search MODULE.

    `import webget.search as sm` is a trap here: the package re-exports the
    `search` function, so the bound name is the function and attribute access
    raises AttributeError. That false failure appeared in this very script.
    """
    import importlib

    return importlib.import_module("webget.search")


def provenance_api_exists():
    """The exact call shape the CLI depends on. Bug 2 was this going missing."""
    sm = _search_module()
    for attr in ("search", "search_with_provenance", "SearchError"):
        if not hasattr(sm, attr):
            raise AssertionError(f"webget.search.{attr} missing")
    return "search, search_with_provenance, SearchError present"


def provenance_shape_offline():
    """Provenance must carry all four keys on a successful call, with no network.

    Drives the real function with a stubbed transport so the assertion is about
    webget's own code, not about ddgs or the network.
    """
    from unittest.mock import patch

    sm = _search_module()
    fake = [{"title": "t", "href": "https://example.com", "body": "b"}]
    with patch("ddgs.DDGS") as D:
        D.return_value.text.return_value = fake
        res, prov = sm.search_with_provenance("q", n=3, engine="brave")
    for key in ("requested", "engine", "failed_over", "tried"):
        if key not in prov:
            raise AssertionError(f"provenance missing {key!r}")
    if not res:
        raise AssertionError("no results returned")
    if prov["engine"] != "brave":
        raise AssertionError(f"engine should be brave, got {prov['engine']!r}")
    if prov["failed_over"] is not False:
        raise AssertionError("failover should not trigger on success")
    if len(D.return_value.text.call_args_list) != 1:
        raise AssertionError("success path made more than one call")
    return "shape ok, single call on success"


def failed_over_is_true_when_all_fail():
    """Bug 1 directly: failed_over must be True when alternates were attempted."""
    from unittest.mock import patch

    sm = _search_module()
    with patch("ddgs.DDGS") as D:
        D.return_value.text.side_effect = RuntimeError("all down")
        try:
            sm.search_with_provenance("q", n=3, engine="brave")
        except Exception as e:
            prov = getattr(e, "provenance", None)
            if prov is None:
                raise AssertionError("error carries no provenance") from e
            if prov.get("failed_over") is not True:
                raise AssertionError(
                    f"failed_over should be True after trying alternates, got {prov.get('failed_over')!r}"
                )
            if not prov.get("tried"):
                raise AssertionError("tried is empty")
            return f"failed_over=True after {len(prov['tried'])} attempts"
    raise AssertionError("no exception raised when every engine failed")


def cli_json_provenance_offline():
    """The CLI's --json path must exist and emit provenance.

    This is the check that would have caught bug 2 (ruff removing the import):
    the suite called the function directly, so a broken import in the CLI was
    invisible. This runs the CLI.
    """

    code = (
        "import sys, json, importlib;"
        "from unittest.mock import patch;"
        "import webget_cli as w;"
        "fake=[{'title':'T','href':'https://e.com','body':'B'}];"
        "D=patch('ddgs.DDGS');"
        "m=D.start(); m.return_value.text.return_value=fake;"
        "sys.argv=['webget','s','q','--json','--limit','1'];"
        "w.main();"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=90)
    out = r.stdout + r.stderr
    if '"engine"' not in out or '"failed_over"' not in out:
        raise AssertionError(f"CLI --json emitted no provenance. output={out[:300]!r}")
    # And it must be valid JSON, since that is the whole point of --json.
    start = out.find("{")
    if start == -1:
        raise AssertionError("no JSON object in output")
    json.loads(out[start:])
    return "--json emits engine + failed_over"


def mcp_module_importable():
    """webget_mcp must import in the mcp extra, not exit."""
    r = subprocess.run(
        [sys.executable, "-c", "import webget_mcp; print('ok')"],
        capture_output=True, text=True, timeout=120,
    )
    if r.returncode != 0:
        raise AssertionError(f"import failed: {r.stderr.strip()[:200]}")
    return "imports"


def search_reaches_network():
    """Non-gating: is the public internet reachable for search at all?"""
    sm = _search_module()
    res, prov = sm.search_with_provenance("python documentation", n=3)
    if not res:
        raise AssertionError(f"no results (tried {prov.get('tried')})")
    return f"{len(res)} results via {prov.get('engine')}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--online", action="store_true",
                    help="also run the live-network check (never gating)")
    args = ap.parse_args()

    cwd = _in_neutral_cwd()
    import os

    os.chdir(cwd)
    sys.path = [p for p in sys.path if Path(p).resolve() != Path(__file__).resolve().parent.parent]
    print(f"smoke run in {cwd}")

    print("\nimport:")
    check("import installed webget", import_installed)
    check("known_engines()", known_engines_present)
    check("search API surface", provenance_api_exists)

    print("\ncli:")
    check("webget --help", cli_help)
    check("CLI --json provenance (bug 2 guard)", cli_json_provenance_offline)

    print("\nprovenance:")
    check("shape on success", provenance_shape_offline)
    check("failed_over when all fail (bug 1 guard)", failed_over_is_true_when_all_fail)

    if args.online:
        print("\nnetwork (non-gating):")
        check("live search", search_reaches_network, gating=False)

    print(f"\n{len(PASSES)} passed, {len(FAILURES)} failed")
    if FAILURES:
        print("\nFAILURES:")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)


if __name__ == "__main__":
    main()
