"""Smoke test: drive webget_mcp.py over stdio like an MCP client would."""
import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    params = StdioServerParameters(
        command=sys.executable,
        args=[os.path.join(os.path.dirname(__file__), "..", "webget_mcp.py")],
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        print("TOOLS:", [t.name for t in tools.tools])

        res = await session.call_tool("search", {"query": "mcp server python", "n": 2})
        payload = json.loads(res.content[0].text)
        print("SEARCH RESULTS:", len(payload))
        for r in payload:
            print(" -", r["title"], "|", r["url"])

        res = await session.call_tool(
            "fetch", {"url": "https://example.com", "strategy": "http", "no_cache": True}
        )
        payload = json.loads(res.content[0].text)
        print("FETCH status:", payload.get("status"), "| method:", payload.get("method"))


if __name__ == "__main__":
    asyncio.run(main())
