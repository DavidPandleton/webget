import asyncio

import webget_mcp


def test_mcp_crawl_validates_limits():
    result = asyncio.run(
        webget_mcp.crawl("https://example.com", "/tmp/frontier.db", max_pages=0)
    )
    assert result["error"] == "max_pages must be between 1 and 1000"


def test_mcp_crawl_rejects_unknown_strategy():
    result = asyncio.run(
        webget_mcp.crawl("https://example.com", "/tmp/frontier.db", strategy="wat")
    )
    assert result["error"] == "unknown strategy: wat"
