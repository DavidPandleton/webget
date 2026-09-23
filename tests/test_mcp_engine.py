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

        def fake_prov(query, n=5, engine=None):
            captured["query"] = query
            captured["n"] = n
            captured["engine"] = engine
            return (
                [{"title": "T", "url": "https://m.example", "snippet": "s"}],
                {"requested": engine or "auto", "engine": engine or "auto", "failed_over": False},
            )

        monkeypatch.setattr(webget_mcp.wg, "search_with_provenance", fake_prov)
        out = _run(webget_mcp.search("q", limit=2, engine="brave"))
        assert captured["engine"] == "brave"
        assert out["results"][0]["url"] == "https://m.example"

    def test_engine_none_default(self, monkeypatch):
        captured = {}

        def fake_prov(query, n=5, engine=None):
            captured["engine"] = engine
            return [], {"requested": "auto", "engine": None, "failed_over": False}

        monkeypatch.setattr(webget_mcp.wg, "search_with_provenance", fake_prov)
        _run(webget_mcp.search("q"))
        assert captured["engine"] is None


class TestMCPSearchFetchEngine:
    def test_engine_param_reaches_search(self, monkeypatch):
        captured = {}

        def fake_prov(query, n=5, engine=None):
            captured["engine"] = engine
            return (
                [{"title": "T", "url": "https://sf.example", "snippet": "s"}],
                {"requested": engine or "auto", "engine": engine or "auto", "failed_over": False},
            )

        async def fake_scrape_many(urls, **kw):
            return {u: {"status": "success", "markdown": "ok"} for u in urls}

        monkeypatch.setattr(webget_mcp.wg, "search_with_provenance", fake_prov)
        monkeypatch.setattr(webget_mcp.wg, "scrape_many", fake_scrape_many)
        out = _run(webget_mcp.search_fetch("q", limit=1, engine="mojeek"))
        assert captured["engine"] == "mojeek"
        assert out["results"][0]["search_title"] == "T"
        assert out["results"][0]["status"] == "success"
