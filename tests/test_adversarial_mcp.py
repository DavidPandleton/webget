"""Adversarial MCP tests: malformed args, invalid URLs, repeated/concurrent
calls, tool failure isolation, SSRF through the MCP surface."""
import asyncio
import json
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent
MCP = ROOT / "webget_mcp.py"


def _run(coro, timeout=60):
    return asyncio.run(asyncio.wait_for(coro, timeout=timeout))


async def _session():
    params = StdioServerParameters(command=sys.executable, args=[str(MCP)])
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield session


class TestMalformedArguments:
    @pytest.mark.parametrize(
        "args",
        [
            {},  # missing url
            {"url": 123},  # wrong type
            {"url": None},
            {"url": "https://example.com", "strategy": None},
            {"url": "https://example.com", "max_chars": "abc"},
            {"url": "https://example.com", "timeout": -5},
        ],
    )
    def test_malformed_arguments_do_not_kill_server(self, args):
        async def run():
            params = StdioServerParameters(command=sys.executable, args=[str(MCP)])
            async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                res = await session.call_tool("fetch", args)
                return res

        res = _run(run())
        # either a structured error payload or an MCP-level error, never a hang
        assert res is not None

    def test_unknown_tool_is_error(self):
        async def run():
            params = StdioServerParameters(command=sys.executable, args=[str(MCP)])
            async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                res = await session.call_tool("nonexistent_tool", {})
                return res

        res = _run(run())
        assert res.isError


class TestInvalidURLs:
    @pytest.mark.parametrize("url", ["not a url", "ftp://x.com", "file:///etc/passwd", ""])
    def test_invalid_url_returns_error_payload(self, url):
        async def run():
            params = StdioServerParameters(command=sys.executable, args=[str(MCP)])
            async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                res = await session.call_tool("fetch", {"url": url, "no_cache": True})
                return res

        res = _run(run())
        assert "error" in res.content[0].text or res.isError


class TestRepeatedCalls:
    def test_ten_repeated_calls_same_session(self):
        async def run():
            params = StdioServerParameters(command=sys.executable, args=[str(MCP)])
            async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                for _ in range(10):
                    res = await session.call_tool(
                        "fetch",
                        {"url": "https://example.com", "strategy": "http", "no_cache": True},
                    )
                    if res.isError:
                        return "ERROR"
                return "OK"

        assert _run(run()) == "OK"

    def test_concurrent_calls(self):
        async def run():
            params = StdioServerParameters(command=sys.executable, args=[str(MCP)])
            async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                results = await asyncio.gather(
                    *[
                        session.call_tool(
                            "fetch",
                            {"url": "https://example.com", "strategy": "http", "no_cache": True},
                        )
                        for _ in range(5)
                    ]
                )
                return [r.isError for r in results]

        assert _run(run()) == [False] * 5


class TestToolFailureIsolation:
    def test_bogus_then_good_same_session(self):
        """A failed call must not poison subsequent calls in the session."""
        async def run():
            params = StdioServerParameters(command=sys.executable, args=[str(MCP)])
            async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                await session.call_tool("fetch", {"url": "https://example.com", "strategy": "bogus"})
                res = await session.call_tool(
                    "fetch", {"url": "https://example.com", "strategy": "http", "no_cache": True}
                )
                return res

        res = _run(run())
        assert "success" in res.content[0].text


class TestSSRFViaMCP:
    def test_private_url_blocked(self, server):
        """MCP fetch of 127.0.0.1 must be blocked by default (SSRF guard)."""
        url = server.url("/private")

        async def run():
            params = StdioServerParameters(command=sys.executable, args=[str(MCP)])
            async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                res = await session.call_tool("fetch", {"url": url, "no_cache": True})
                return res

        res = _run(run())
        assert "private" in res.content[0].text.lower()

    def test_search_output_shape(self):
        """search must return list of dicts with title/url/snippet."""
        async def run():
            params = StdioServerParameters(command=sys.executable, args=[str(MCP)])
            async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                res = await session.call_tool("search", {"query": "python mcp server", "n": 2})
                return res

        res = _run(run())
        payload = json.loads(res.content[0].text)
        assert isinstance(payload, list)
        if payload:
            assert {"title", "url", "snippet"} <= set(payload[0])
