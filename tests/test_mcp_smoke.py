"""Smoke test: drive webget_mcp.py over stdio like an MCP client would.

Network-light: search results are asserted as a list (may be empty when
DuckDuckGo is rate-limiting), fetch targets example.com over the http path.
"""

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

MCP = Path(__file__).resolve().parent.parent / "webget_mcp.py"


async def _main():
    params = StdioServerParameters(command=sys.executable, args=[str(MCP)])
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        print("TOOLS:", [t.name for t in tools.tools])
        assert {t.name for t in tools.tools} >= {"search", "fetch", "search_fetch"}

        res = await session.call_tool("search", {"query": "mcp server python", "n": 2})
        payload = json.loads(res.content[0].text)
        print("SEARCH RESULTS:", len(payload))
        assert isinstance(payload, list)

        res = await session.call_tool(
            "fetch", {"url": "https://example.com", "strategy": "http", "no_cache": True}
        )
        payload = json.loads(res.content[0].text)
        print("FETCH status:", payload.get("status"), "| method:", payload.get("method"))
        assert payload.get("status") == "success"
        assert payload.get("method") == "http"


def test_mcp_smoke():
    asyncio.run(asyncio.wait_for(_main(), timeout=60))


if __name__ == "__main__":
    asyncio.run(_main())
