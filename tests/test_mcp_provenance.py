"""Tests for engine provenance through the MCP tools.

This is the surface where provenance matters MOST: an MCP agent never sees
stderr, so a failover warning is invisible to it. If the payload does not
say which engine answered, the agent cannot tell that the results came from
somewhere other than what it asked for.
"""

from __future__ import annotations

import asyncio

import webget_mcp


def _run(coro):
    return asyncio.run(coro)


class TestMCPSearchFetchProvenance:
    def test_search_fetch_reports_engine(self, monkeypatch):
        def fake_prov(query, n=5, engine=None):
            return (
                [{"title": "T", "url": "https://sf.example/", "snippet": "s"}],
                {"requested": "google", "engine": "brave", "failed_over": True},
            )

        async def fake_scrape_many(urls, **kw):
            return {u: {"status": "success", "markdown": "ok"} for u in urls}

        monkeypatch.setattr(webget_mcp.wg, "search_with_provenance", fake_prov)
        monkeypatch.setattr(webget_mcp.wg, "scrape_many", fake_scrape_many)
        out = _run(webget_mcp.search_fetch("q", n=1, engine="google"))
        assert out["engine"] == "brave"
        assert out["requested_engine"] == "google"
        assert out["failed_over"] is True
        assert out["results"][0]["search_title"] == "T"


class TestMCPSearchProvenance:
    def test_result_carries_engine_when_available(self, monkeypatch):
        def fake_prov(query, n=5, engine=None):
            return (
                [{"title": "T", "url": "https://m.example/", "snippet": "s"}],
                {
                    "requested": "google",
                    "engine": "brave",
                    "failed_over": True,
                    "tried": ["google", "brave"],
                    "first_error": "No results found.",
                },
            )

        monkeypatch.setattr(webget_mcp.wg, "search_with_provenance", fake_prov)
        out = _run(webget_mcp.search("q", n=2, engine="google"))
        assert out["engine"] == "brave"
        assert out["requested_engine"] == "google"
        assert out["failed_over"] is True
        assert out["results"][0]["url"] == "https://m.example/"

    def test_no_failover_reports_engine_as_requested(self, monkeypatch):
        def fake_prov(query, n=5, engine=None):
            return (
                [{"title": "T", "url": "https://m.example/", "snippet": "s"}],
                {"requested": "brave", "engine": "brave", "failed_over": False},
            )

        monkeypatch.setattr(webget_mcp.wg, "search_with_provenance", fake_prov)
        out = _run(webget_mcp.search("q", engine="brave"))
        assert out["engine"] == "brave"
        assert out["failed_over"] is False

    def test_falls_back_to_plain_search_when_provenance_missing(self, monkeypatch):
        """Older shims may expose only `search`; must not break."""
        monkeypatch.setattr(
            webget_mcp.wg,
            "search",
            lambda query, n=5, engine=None: [
                {"title": "T", "url": "https://m.example/", "snippet": "s"}
            ],
        )
        monkeypatch.delattr(webget_mcp.wg, "search_with_provenance", raising=False)
        out = _run(webget_mcp.search("q", engine="brave"))
        assert out["results"][0]["url"] == "https://m.example/"
        assert out["engine"] == "brave"  # best-effort, matches the request

    def test_clamp_error_still_returns_error_shape(self):
        out = _run(webget_mcp.search("q", n=10**9))
        assert isinstance(out, dict)
        assert "error" in out
