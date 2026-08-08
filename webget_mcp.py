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
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import webget_cli as wg

try:
    from fastmcp import FastMCP
except ImportError:  # pragma: no cover
    sys.exit("webget_mcp requires the 'fastmcp' package: pip install fastmcp")

mcp = FastMCP("webget")

_VALID_STRATEGIES = ("auto", "http", "crawl4ai", "firecrawl")
_MAX_SEARCH_N = 50
_MAX_MAX_CHARS = 1_000_000
_MAX_TIMEOUT = 120


def _clamp(name, value, lo, hi):
    """Reject out-of-range numeric input instead of silently accepting
    abusive values (n=10**9, max_chars=10**9, timeout=-5)."""
    if not isinstance(value, int):
        return f"{name} must be an integer"
    if value < lo or value > hi:
        return f"{name} must be between {lo} and {hi}"
    return None


def _validate_profile(profile):
    """Validate a profile name for authenticated fetches.

    Returns an error string, or None when the profile exists and is usable.
    Invalid names (path traversal etc.) and unknown profiles are HARD errors:
    a silent anonymous fallback would return login_required while the agent
    believes its session was used.
    """
    try:
        pdir = wg.profile_dir(profile)  # raises SystemExit on invalid names
    except SystemExit:
        return f"invalid profile name: {profile!r}"
    if not os.path.isdir(pdir):
        return f"profile '{profile}' not found"
    return None


@mcp.tool()
async def search(query: str, n: int = 5) -> list[dict]:
    """Search the web (DuckDuckGo). Returns up to n results with title/url/snippet."""
    err = _clamp("n", n, 1, _MAX_SEARCH_N)
    if err:
        return [{"error": err}]
    return await asyncio.to_thread(wg.search, query, n)


@mcp.tool()
def list_profiles() -> list[dict]:
    """List locally stored login sessions (profiles) with non-sensitive
    metadata: name, last used, size, status. Cookie values are never
    returned. Use with fetch(profile=...) to scrape authenticated pages."""
    return wg.list_profiles()


@mcp.tool()
async def fetch(
    url: str,
    max_chars: int = 10000,
    timeout: int = 20,
    strategy: str = "auto",
    no_cache: bool = False,
    profile: str | None = None,
) -> dict:
    """Scrape a single URL to markdown, with metadata.

    strategy: auto|http|crawl4ai|firecrawl. Status values:
    success|login_required|challenge|blocked|error.

    profile: name of a locally stored login session (created with
    'webget login URL --profile NAME'). Invalid names and unknown
    profiles are hard errors (never a silent anonymous fallback).
    """
    if strategy not in _VALID_STRATEGIES:
        return {"status": "error", "error": f"unknown strategy: {strategy}"}
    if strategy == "firecrawl" and not wg.firecrawl_key():
        return {"status": "error", "error": "WEBGET_FIRECRAWL_KEY not set"}
    for name, value, lo, hi in (
        ("max_chars", max_chars, 100, _MAX_MAX_CHARS),
        ("timeout", timeout, 1, _MAX_TIMEOUT),
    ):
        err = _clamp(name, value, lo, hi)
        if err:
            return {"status": "error", "error": err}
    if profile is not None:
        err = _validate_profile(profile)
        if err:
            return {"status": "error", "error": err}
    res = await wg.scrape_many(
        [url],
        max_chars=max_chars,
        per_url_timeout=timeout,
        strategy=strategy,
        no_cache=no_cache,
        profile=profile,
    )
    return res.get(url, {})


@mcp.tool()
async def search_fetch(
    query: str,
    n: int = 3,
    max_chars: int = 4000,
    timeout: int = 20,
    no_cache: bool = False,
    profile: str | None = None,
) -> list[dict]:
    """Search the web, then scrape the top n results in parallel.

    Returns one entry per URL with rank, search snippet, and scrape result
    (status/method/markdown).

    profile: name of a locally stored login session (created with
    'webget login URL --profile NAME'). Invalid names and unknown
    profiles are hard errors (never a silent anonymous fallback).
    """
    for name, value, lo, hi in (
        ("n", n, 1, _MAX_SEARCH_N),
        ("max_chars", max_chars, 100, _MAX_MAX_CHARS),
        ("timeout", timeout, 1, _MAX_TIMEOUT),
    ):
        err = _clamp(name, value, lo, hi)
        if err:
            return [{"error": err}]
    if profile is not None:
        err = _validate_profile(profile)
        if err:
            return [{"error": err}]
    results = await asyncio.to_thread(wg.search, query, n)
    urls = [r["url"] for r in results]
    scraped = await wg.scrape_many(
        urls,
        max_chars=max_chars,
        per_url_timeout=timeout,
        no_cache=no_cache,
        profile=profile,
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
