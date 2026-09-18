#!/usr/bin/env python3
"""Drive webget_mcp.py over stdio exactly like a real MCP client would."""
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

MCP = str(Path(__file__).resolve().parent.parent / "webget_mcp.py")


def tool_failed(result):
    """Cross-SDK error check: is_error (mcp 2.x) or isError (mcp 1.x)."""
    for attr in ("is_error", "isError"):
        val = getattr(result, attr, None)
        if val is not None:
            return bool(val)
    return False


async def main():
    params = StdioServerParameters(command=sys.executable, args=[MCP])
    async with stdio_client(params) as (read, write), ClientSession(read, write) as s:
        await s.initialize()

        tools = await s.list_tools()
        names = sorted(t.name for t in tools.tools)
        print("TOOLS:", ", ".join(names))
        assert {"search", "fetch", "search_fetch", "map", "login",
                "list_profiles"} <= set(names), "missing tools"

        # --- fetch local test server (offline determinism) ---
        res = await s.call_tool(
            "fetch", {"url": "https://example.com", "strategy": "http", "no_cache": True}
        )
        payload = json.loads(res.content[0].text)
        print(f"FETCH: status={payload.get('status')} method={payload.get('method')} "
              f"title={str(payload.get('title'))[:40]!r}")
        assert payload.get("status") == "success"

        # --- search with explicit engine (live) ---
        res = await s.call_tool(
            "search", {"query": "python asyncio tutorial", "n": 3, "engine": "yandex"}
        )
        payload = json.loads(res.content[0].text)
        if tool_failed(res):
            print(f"SEARCH yandex: error payload (network?) -> {str(payload)[:120]}")
        else:
            print(f"SEARCH yandex: engine={payload.get('engine')} "
                  f"requested={payload.get('requested_engine')} "
                  f"failed_over={payload.get('failed_over')} n={len(payload.get('results', []))}")
            for i, r in enumerate(payload.get("results", [])[:2], 1):
                print(f"  {i}. {r.get('title','')[:60]}  {r.get('url','')[:60]}")

        # --- search auto (live, provenance matters most here) ---
        res = await s.call_tool("search", {"query": "linux kernel", "n": 2})
        payload = json.loads(res.content[0].text)
        if tool_failed(res):
            print(f"SEARCH auto: error payload -> {str(payload)[:120]}")
        else:
            print(f"SEARCH auto: engine={payload.get('engine')} "
                  f"failed_over={payload.get('failed_over')} n={len(payload.get('results', []))}")

        # --- list_profiles tool (offline) ---
        res = await s.call_tool("list_profiles", {})
        payload = json.loads(res.content[0].text) if not tool_failed(res) else None
        print(f"PROFILES: {str(payload)[:100]}")

    print("SEMUA CALL OK")


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=120))
