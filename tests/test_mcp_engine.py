"""Tests for the MCP surface of multi-engine search.

FastMCP tools are async; calling them directly (no stdio server) is enough
to pin the engine pass-through contract. wg.search is looked up on the shim
at call time, so patching webget_mcp.wg.search reaches it.
"""

import asyncio

import webget_mcp


def _run(coro):
    return asyncio.run(coro)


class TestMCPSearchEngine:
    def test_engine_param_reaches_search(self, monkeypatch):
        captured = {}

        def fake_search(query, n=5, engine=None):
            captured["query"] = query
            captured["n"] = n
            captured["engine"] = engine
            return [{"title": "T", "url": "https://m.example", "snippet": "s"}]

        monkeypatch.setattr(webget_mcp.wg, "search", fake_search)
        out = _run(webget_mcp.search("q", n=2, engine="brave"))
        assert captured["engine"] == "brave"
        assert out[0]["url"] == "https://m.example"

    def test_engine_none_default(self, monkeypatch):
        captured = {}

        monkeypatch.setattr(
            webget_mcp.wg,
            "search",
            lambda query, n=5, engine=None: captured.update(engine=engine) or [],
        )
        _run(webget_mcp.search("q"))
        assert captured["engine"] is None


class TestMCPSearchFetchEngine:
    def test_engine_param_reaches_search(self, monkeypatch):
        captured = {}

        def fake_search(query, n=5, engine=None):
            captured["engine"] = engine
            return [{"title": "T", "url": "https://sf.example", "snippet": "s"}]

        async def fake_scrape_many(urls, **kw):
            return {u: {"status": "success", "markdown": "ok"} for u in urls}

        monkeypatch.setattr(webget_mcp.wg, "search", fake_search)
        monkeypatch.setattr(webget_mcp.wg, "scrape_many", fake_scrape_many)
        out = _run(webget_mcp.search_fetch("q", n=1, engine="mojeek"))
        assert captured["engine"] == "mojeek"
        assert out and out[0]["search_title"] == "T"
        assert out[0]["status"] == "success"
