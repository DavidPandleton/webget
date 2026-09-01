"""Integration: MCP fetch with a real profile uses the stored session.

A cookie-gated local server returns 200 only when the request carries the
session cookie from a profile's storage_state.json:
  - fetch(profile="testp")  -> success (session used)
  - fetch() (anonymous)     -> blocked/login_required (no session)

Spawns webget_mcp.py as a stdio subprocess with WEBGET_PROFILE_DIR pointing
at a temp profile root and WEBGET_ALLOW_PRIVATE=1 (the test server lives on
127.0.0.1, which the SSRF guard blocks by default).
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent
MCP = ROOT / "webget_mcp.py"
sys.path.insert(0, str(ROOT))

from tests.http_server import TestServer


def _spawn(profile_root):
    env = {
        **os.environ,
        "WEBGET_PROFILE_DIR": str(profile_root),
        "WEBGET_ALLOW_PRIVATE": "1",
    }
    return StdioServerParameters(command=sys.executable, args=[str(MCP)], env=env)


def _make_profile(root, name):
    d = root / name
    (d / "Default").mkdir(parents=True)
    (d / "storage_state.json").write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        "name": "session",
                        "value": "1",
                        "domain": "127.0.0.1",
                        "path": "/",
                        "expires": 9999999999,
                    }
                ]
            }
        )
    )


def test_mcp_fetch_with_profile_uses_session(tmp_path):
    server = TestServer().start()
    try:
        root = tmp_path / "profiles"
        _make_profile(root, "testp")
        url = server.url("/cookie-gated")

        async def run():
            async with (
                stdio_client(_spawn(root)) as (read, write),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                ok = await session.call_tool(
                    "fetch", {"url": url, "strategy": "http", "no_cache": True, "profile": "testp"}
                )
                anon = await session.call_tool(
                    "fetch", {"url": url, "strategy": "http", "no_cache": True}
                )
                return ok, anon

        ok, anon = asyncio.run(asyncio.wait_for(run(), timeout=60))
        ok_text = ok.content[0].text if ok.content else ""
        anon_text = anon.content[0].text if anon.content else ""
        assert not ok.isError, ok_text
        assert '"status":"success"' in ok_text, ok_text
        assert "you are authenticated" in ok_text, ok_text
        assert '"authenticated":true' in ok_text, ok_text  # session was used
        # anonymous fetch must NOT succeed on the gated page
        assert ok_text != anon_text
        assert '"status":"success"' not in anon_text, f"anonymous fetch leaked: {anon_text}"
    finally:
        server.stop()


def test_mcp_fetch_unknown_profile_is_hard_error(tmp_path):
    """Even with a profile root, a name that does not exist is an error
    (never a silent anonymous fallback)."""

    async def run():
        env = {**os.environ, "WEBGET_PROFILE_DIR": str(tmp_path / "profiles")}
        params = StdioServerParameters(command=sys.executable, args=[str(MCP)], env=env)
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool(
                "fetch", {"url": "https://example.com", "profile": "ghost", "no_cache": True}
            )
            return res

    res = asyncio.run(asyncio.wait_for(run(), timeout=60))
    assert not res.isError
    assert "profile 'ghost' not found" in res.content[0].text


def test_mcp_fetch_enriched_output_present(tmp_path):
    """MCP fetch must include auth, attempts, and reasons in the response.

    These fields give agents full provenance: whether the fetch used a
    session, how many ladder steps were tried, and the full chain of
    failures. Added 2026-08-31.
    """
    server = TestServer().start()
    try:
        root = tmp_path / "profiles"
        _make_profile(root, "testp")
        gated = server.url("/cookie-gated")

        async def run():
            async with (
                stdio_client(_spawn(root)) as (read, write),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                # Authenticated fetch
                ok = await session.call_tool(
                    "fetch",
                    {
                        "url": gated,
                        "strategy": "http",
                        "no_cache": True,
                        "profile": "testp",
                    },
                )
                # Anonymous fetch (blocked)
                anon = await session.call_tool(
                    "fetch",
                    {"url": gated, "strategy": "http", "no_cache": True},
                )
                return ok, anon

        ok, anon = asyncio.run(asyncio.wait_for(run(), timeout=60))
        ok_text = ok.content[0].text if ok.content else ""
        anon_text = anon.content[0].text if anon.content else ""

        # Authenticated: standard assertions
        assert not ok.isError, ok_text
        assert '"status":"success"' in ok_text
        # auth
        assert '"auth"' in ok_text
        assert '"authenticated":true' in ok_text
        assert '"attempts"' in ok_text
        # Success carries an empty reasons list (consistent shape: consumers
        # can always iterate reasons without a None check).
        assert '"reasons":[]' in ok_text, "success should have empty reasons"

        # Anonymous (blocked): must have provenance fields
        assert '"status":"blocked"' in anon_text or '"status":"login_required"' in anon_text
        assert '"auth"' in anon_text
        assert '"authenticated":false' in anon_text or '"authenticated":null' in anon_text
        assert '"attempts"' in anon_text
        assert '"reasons"' in anon_text
        assert '"state":"blocked"' in anon_text or '"state":"login_required"' in anon_text
    finally:
        server.stop()


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
