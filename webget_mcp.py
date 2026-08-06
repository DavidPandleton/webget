#!/usr/bin/env python3
"""webget MCP server - expose webget's search + scrape ladder to MCP clients
(including opencode). Run with:

    python webget_mcp.py

and register in opencode.json as a local MCP server:

    {
      "mcp": {
        "webget": {
          "type": "local",
          "command": ["python", "/path/to/webget_mcp.py"],
          "enabled": true
        }
      }
    }

Dependencies: mcp (pip install mcp). webget_cli.py must live next to this file.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import webget_cli as wg

try:
    from fastmcp import FastMCP
except ImportError:  # pragma: no cover
    sys.exit("webget_mcp requires the 'fastmcp' package: pip install fastmcp")

mcp = FastMCP("webget")


@mcp.tool()
async def search(query: str, n: int = 5) -> list[dict]:
    """Search the web (DuckDuckGo). Returns up to n results with title/url/snippet."""
    return await asyncio.to_thread(wg.search, query, n)


@mcp.tool()
async def fetch(
    url: str,
    max_chars: int = 10000,
    timeout: int = 20,
    strategy: str = "auto",
    no_cache: bool = False,
) -> dict:
    """Scrape a single URL to markdown, with metadata.

    strategy: auto|http|crawl4ai|firecrawl. Status values:
    success|login_required|challenge|blocked|error.
    """
    res = await wg.scrape_many(
        [url],
        max_chars=max_chars,
        per_url_timeout=timeout,
        strategy=strategy,
        no_cache=no_cache,
    )
    return res.get(url, {})


@mcp.tool()
async def search_fetch(
    query: str,
    n: int = 3,
    max_chars: int = 4000,
    timeout: int = 20,
    no_cache: bool = False,
) -> list[dict]:
    """Search the web, then scrape the top n results in parallel.

    Returns one entry per URL with rank, search snippet, and scrape result
    (status/method/markdown).
    """
    results = await asyncio.to_thread(wg.search, query, n)
    urls = [r["url"] for r in results]
    scraped = await wg.scrape_many(
        urls,
        max_chars=max_chars,
        per_url_timeout=timeout,
        no_cache=no_cache,
    )
    out = []
    for i, r in enumerate(results):
        got = scraped.get(r["url"], {})
        out.append(
            {
                "rank": i + 1,
                "search_title": r["title"],
                "snippet": r.get("snippet", ""),
                "scrape_title": got.get("title", ""),
                "markdown": got.get("markdown", ""),
                "status": got.get("status", ""),
                "method": got.get("method", ""),
                "cached": got.get("cached", False),
                "error": got.get("error"),
            }
        )
    return out


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
