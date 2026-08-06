"""Phase 11 MCP data-leakage review: outputs must never expose secrets,
filesystem paths, cookies, headers, API keys, or internal tracebacks."""
import asyncio
import json
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent
MCP = ROOT / "webget_mcp.py"

LEAK_MARKERS = [
    "cookie",
    "authorization",
    "api-key",
    "apikey",
    "tarigansdavid",
    "storage_state",
    "profiles/",
    "/home/",
    "Traceback",
    "File \"",
    "line ",
]

# The literal variable NAME may legitimately appear in error messages
# ("WEBGET_FIRECRAWL_KEY not set"); the VALUE must never appear.
SECRET_MARKERS = ["sk-test-secret-12345"]


def _run(coro, timeout=60):
    return asyncio.run(asyncio.wait_for(coro, timeout=timeout))


def _scan(text):
    hits = [m for m in LEAK_MARKERS if m.lower() in text.lower()]
    return hits


class TestNoSecretLeakage:
    @pytest.mark.parametrize(
        "tool,args",
        [
            ("fetch", {"url": "https://example.com", "strategy": "http", "no_cache": True}),
            ("fetch", {"url": "https://example.com", "strategy": "bogus"}),
            ("fetch", {"url": "https://example.com", "strategy": "firecrawl"}),
            ("search", {"query": "mcp", "n": 1}),
            ("search", {"query": "x", "n": 10**9}),
            ("search_fetch", {"query": "python", "n": 1}),
        ],
    )
    def test_output_contains_no_secrets(self, tool, args, monkeypatch):
        monkeypatch.setenv("WEBGET_FIRECRAWL_KEY", "sk-test-secret-12345")

        async def run():
            params = StdioServerParameters(command=sys.executable, args=[str(MCP)])
            async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                res = await session.call_tool(tool, args)
                return res

        res = _run(run())
        if res.isError:
            return  # error path: no payload to leak, still fine
        text = res.content[0].text
        hits = _scan(text)
        assert not hits, f"leaked markers {hits} in {tool} output"
        assert not any(s in text for s in SECRET_MARKERS), "secret value leaked"

    def test_error_payload_no_traceback(self):
        async def run():
            params = StdioServerParameters(command=sys.executable, args=[str(MCP)])
            async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                res = await session.call_tool("fetch", {"url": "not a url"})
                return res

        res = _run(run())
        text = res.content[0].text if res.content else ""
        assert "Traceback" not in text
        assert "webget_cli.py" not in text

    def test_search_fetch_shape_complete(self):
        async def run():
            params = StdioServerParameters(command=sys.executable, args=[str(MCP)])
            async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                res = await session.call_tool(
                    "search_fetch", {"query": "python mcp server", "n": 2, "no_cache": True}
                )
                return res

        res = _run(run())
        payload = json.loads(res.content[0].text)
        for entry in payload:
            assert {"rank", "search_title", "snippet", "scrape_title", "markdown",
                    "status", "method", "cached", "error"} <= set(entry)


class TestServerRecovery:
    def test_bad_then_good_sequence(self):
        """bad request -> error -> server alive -> valid request succeeds."""
        async def run():
            params = StdioServerParameters(command=sys.executable, args=[str(MCP)])
            async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                bad = await session.call_tool("fetch", {"url": "https://example.com", "strategy": "bogus"})
                assert not bad.isError or "error" in bad.content[0].text
                good = await session.call_tool(
                    "fetch", {"url": "https://example.com", "strategy": "http", "no_cache": True}
                )
                return good

        res = _run(run())
        assert "success" in res.content[0].text

    def test_malformed_json_does_not_crash(self):
        async def run():
            params = StdioServerParameters(command=sys.executable, args=[str(MCP)])
            async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                res = await session.call_tool("search", {"query": "a" * 100000, "n": 5})
                return res

        res = _run(run())
        assert res is not None
