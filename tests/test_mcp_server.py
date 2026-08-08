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

    assert _run(run()) == ["search", "fetch", "search_fetch"]


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


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
