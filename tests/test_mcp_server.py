"""MCP server regression tests.

Spawns webget_mcp.py as a stdio subprocess and drives it like an MCP
client would. Requires the mcp client SDK (installed via the [mcp] extra).
"""

import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent
MCP = ROOT / "webget_mcp.py"


def _run(coro, timeout=60):
    """Run a coroutine with a hard wall-clock cap so a dead server
    (the bug this guards against) fails the test instead of hanging CI."""
    return asyncio.run(asyncio.wait_for(coro, timeout=timeout))


def test_tools_listed():
    async def run():
        params = StdioServerParameters(command=sys.executable, args=[str(MCP)])
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            return [t.name for t in tools.tools]

    # order is not a contract; membership is
    assert sorted(_run(run())) == sorted(
        ["search", "fetch", "search_fetch", "list_profiles", "login"]
    )


def test_invalid_strategy_returns_error_not_crash():
    """The server must survive a client sending strategy='bogus'.

    Before the guard, _ladder raised SystemExit (a BaseException) which
    killed the whole server process and hung the client.
    """

    async def run():
        params = StdioServerParameters(command=sys.executable, args=[str(MCP)])
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool(
                "fetch",
                {"url": "https://example.com", "strategy": "bogus", "no_cache": True},
            )
            return res

    res = _run(run())
    assert not res.isError
    assert "error" in res.content[0].text


def test_firecrawl_without_key_returns_error_not_crash():
    """strategy='firecrawl' without WEBGET_FIRECRAWL_KEY must not kill the
    server either (SystemExit from _ladder)."""

    async def run():
        params = StdioServerParameters(command=sys.executable, args=[str(MCP)])
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool(
                "fetch",
                {"url": "https://example.com", "strategy": "firecrawl", "no_cache": True},
            )
            return res

    res = _run(run())
    assert not res.isError
    assert "error" in res.content[0].text


def test_server_stays_alive_after_bad_calls():
    """The same server process that handled a bad strategy call must still
    answer a normal fetch afterwards."""

    async def run():
        params = StdioServerParameters(command=sys.executable, args=[str(MCP)])
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            await session.call_tool(
                "fetch",
                {"url": "https://example.com", "strategy": "bogus", "no_cache": True},
            )
            res = await session.call_tool(
                "fetch",
                {"url": "https://example.com", "strategy": "http", "no_cache": True},
            )
            return res

    res = _run(run())
    assert not res.isError
    assert "success" in res.content[0].text


def test_fetch_invalid_profile_returns_error_not_crash():
    """profile name with path traversal must return a clean error and the
    server must stay alive (SystemExit from profile_dir must not escape)."""

    async def run():
        params = StdioServerParameters(command=sys.executable, args=[str(MCP)])
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool(
                "fetch",
                {"url": "https://example.com", "profile": "../etc", "no_cache": True},
            )
            ok = await session.call_tool(
                "fetch",
                {"url": "https://example.com", "strategy": "http", "no_cache": True},
            )
            return res, ok

    res, ok = _run(run())
    assert not res.isError
    assert "invalid profile name" in res.content[0].text
    assert "success" in ok.content[0].text  # server alive after the bad call


def test_fetch_nonexistent_profile_returns_error():
    """A valid-looking profile that does not exist is a hard error, never a
    silent anonymous fallback (which would return login_required while the
    agent believes the session was used)."""

    async def run():
        params = StdioServerParameters(command=sys.executable, args=[str(MCP)])
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool(
                "fetch",
                {"url": "https://example.com", "profile": "ghost", "no_cache": True},
            )
            return res

    res = _run(run())
    assert not res.isError
    assert "profile 'ghost' not found" in res.content[0].text


def test_search_fetch_invalid_profile_returns_error_not_crash():
    """search_fetch must validate profile the same way and stay alive."""

    async def run():
        params = StdioServerParameters(command=sys.executable, args=[str(MCP)])
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool(
                "search_fetch",
                {"query": "test", "n": 1, "profile": "../etc"},
            )
            ok = await session.call_tool(
                "search_fetch",
                {"query": "example.com", "n": 1},
            )
            return res, ok

    res, ok = _run(run())
    assert not res.isError
    assert "invalid profile name" in res.content[0].text
    assert not ok.isError  # server alive after the bad call


def test_list_profiles_tool_metadata_only(tmp_path):
    """list_profiles must list the session and NEVER leak cookie values."""
    import json
    import os

    root = tmp_path / "profiles"
    d = root / "sion"
    (d / "Default").mkdir(parents=True)
    (d / "storage_state.json").write_text(
        json.dumps(
            {"cookies": [{"name": "s", "value": "SUPERSECRET", "domain": ".x.com", "expires": -1}]}
        )
    )

    async def run():
        env = {**os.environ, "WEBGET_PROFILE_DIR": str(root)}
        params = StdioServerParameters(command=sys.executable, args=[str(MCP)], env=env)
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool("list_profiles", {})
            return res

    res = _run(run())
    assert not res.isError
    text = res.content[0].text
    assert "sion" in text
    assert "SUPERSECRET" not in text  # cookie values never exposed


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
