"""Ide #1: metadata passthrough in the MCP tool layer.

Moved out of tests/test_metadata.py: these import webget_mcp, which needs
fastmcp/mcp, so they run in the mcp-test CI job (unit job has no fastmcp).
"""


class TestMcpMetadataExposure:
    def test_mcp_fetch_passthrough_has_metadata(self, fresh_cache):
        import asyncio

        import webget_mcp

        res = asyncio.run(
            webget_mcp.fetch(fresh_cache.url("/long"), strategy="http", no_cache=True)
        )
        assert set(res["metadata"]) == {"author", "published_at", "site_name", "language"}

    def test_mcp_search_fetch_rebuild_has_metadata(self, fresh_cache, monkeypatch):
        import asyncio

        import webget_mcp

        monkeypatch.setattr(
            webget_mcp.wg,
            "search",
            lambda *a, **k: [{"title": "T", "url": fresh_cache.url("/long"), "snippet": "s"}],
        )
        out = asyncio.run(webget_mcp.search_fetch("q", n=1, no_cache=True))
        assert set(out[0]["metadata"]) == {
            "author",
            "published_at",
            "site_name",
            "language",
        }
