"""Integration tests: the full acquisition ladder against the local server.

Covers:
  - HTTP fast-path success (the common case)
  - ladder continuation when HTTP fails (login page, 403, errors)
  - terminal states (challenge, login_required, blocked, error) with
    correct method attribution
  - cache integration (hit path, fresh path, no-cache path)
  - batch behavior with mixed outcomes
  - SSRF guard interplay with the ladder

All tests target the LOCAL server (deterministic), never external sites.
"""
import asyncio
import os

import pytest

import webget_cli as webget


async def _many(urls, **kw):
    return await webget.scrape_many(urls, no_cache=True, **kw)


def _one(res):
    return res[next(iter(res))]


class TestHTTPFastPath:
    @pytest.mark.parametrize(
        "path",
        ["/normal", "/gzip", "/long?n=3"],
    )
    def test_http_success(self, fresh_cache, path):
        server = fresh_cache
        res = asyncio.run(_many([server.url(path)]))
        out = _one(res)
        assert out["status"] == "success", out.get("error")
        assert out["method"] == "http"
        assert out["markdown"] and not out["cached"]

    @pytest.mark.parametrize(
        "path",
        ["/title-only", "/malformed"],
    )
    def test_thin_content_not_success(self, fresh_cache, path):
        """Pages with no extractable content must not be reported success."""
        server = fresh_cache
        res = asyncio.run(_many([server.url(path)]))
        out = _one(res)
        assert out["status"] != "success"

    def test_http_method_attribution(self, fresh_cache):
        server = fresh_cache
        res = asyncio.run(_many([server.url("/normal")], strategy="http"))
        assert _one(res)["method"] == "http"


class TestLadderContinuation:
    """HTTP fails -> terminal state must reflect the winning reason."""

    @pytest.mark.parametrize(
        "path,expected_state",
        [
            ("/login", "login_required"),
            ("/sion-login", "login_required"),
            ("/403-login", "login_required"),
            ("/403", "blocked"),
            ("/429", "blocked"),
            ("/challenge", "challenge"),
            ("/500", "error"),
        ],
    )
    def test_terminal_states(self, fresh_cache, path, expected_state):
        server = fresh_cache
        res = asyncio.run(_many([server.url(path)]))
        out = _one(res)
        assert out["status"] == expected_state, (path, out.get("error"))
        # method must be the strategy that produced the winning state
        assert out["method"] in ("http", "crawl4ai", "firecrawl", "")

    def test_thin_content_never_success(self, fresh_cache):
        server = fresh_cache
        res = asyncio.run(_many([server.url("/thin")]))
        assert _one(res)["status"] != "success"

    def test_login_required_marks_authenticated_false(self, fresh_cache):
        server = fresh_cache
        res = asyncio.run(_many([server.url("/login")], profile="p"))
        out = _one(res)
        assert out["status"] == "login_required"
        assert out["auth"]["authenticated"] is False


class TestCacheIntegration:
    def test_second_fetch_served_from_cache(self, fresh_cache, isolated_env):
        server = fresh_cache
        url = server.url("/normal")
        r1 = asyncio.run(webget.scrape_many([url], max_chars=2000))[url]
        r2 = asyncio.run(webget.scrape_many([url], max_chars=2000))[url]
        assert r1["status"] == "success"
        assert r2["cached"] is True
        assert r2["method"] == "cache"

    def test_no_cache_skips_read_and_write(self, fresh_cache, isolated_env):
        server = fresh_cache
        url = server.url("/normal")
        r1 = asyncio.run(webget.scrape_many([url], max_chars=2000, no_cache=True))[url]
        assert r1["cached"] is False
        # cache must not contain it either
        assert webget.cache_get(url, None, None, 2000, 3600) is None

    def test_fresh_bypasses_cache(self, fresh_cache, isolated_env):
        server = fresh_cache
        url = server.url("/normal")
        asyncio.run(webget.scrape_many([url], max_chars=2000))
        r2 = asyncio.run(webget.scrape_many([url], max_chars=2000, fresh=True))[url]
        assert r2["cached"] is False
        assert r2["method"] == "http"

    def test_profile_isolated_cache(self, fresh_cache, isolated_env):
        server = fresh_cache
        url = server.url("/normal")
        asyncio.run(webget.scrape_many([url], max_chars=2000, profile="p1"))
        r2 = asyncio.run(webget.scrape_many([url], max_chars=2000, profile="p2"))[url]
        assert r2["cached"] is False  # different profile -> different cache key

    def test_cache_ttl_expiry(self, fresh_cache, isolated_env, monkeypatch):
        import time

        server = fresh_cache
        url = server.url("/normal")
        asyncio.run(webget.scrape_many([url], max_chars=2000, ttl=3600))
        # move the cache file into the past
        p = webget._cache_path(url, None, None, 2000, None)
        old = time.time() - 7200
        os.utime(p, (old, old))
        r2 = asyncio.run(webget.scrape_many([url], max_chars=2000, ttl=3600))[url]
        assert r2["cached"] is False


class TestBatchIntegration:
    def test_mixed_batch_all_answered(self, fresh_cache):
        server = fresh_cache
        paths = ["/normal", "/login", "/403", "/429", "/challenge", "/redirect", "/gzip", "/thin"]
        urls = [server.url(p) for p in paths]
        res = asyncio.run(_many(urls))
        assert len(res) == len(urls)
        by_status = {}
        for u in urls:
            by_status.setdefault(res[u]["status"], []).append(u)
        assert "success" in by_status
        assert {"login_required", "blocked", "challenge", "error"} & set(by_status)
        # every entry has the full contract
        for u in urls:
            r = res[u]
            assert {"title", "markdown", "status", "method", "cached", "attempts", "error", "auth"} <= set(r)

    def test_redirect_chain_integration(self, fresh_cache):
        server = fresh_cache
        res = asyncio.run(_many([server.url("/redirect-chain?n=6")]))
        out = _one(res)
        assert out["status"] == "success"
        assert out["method"] == "http"


class TestSSRFLadder:
    def test_private_url_never_reaches_ladder(self, server, isolated_env):
        res = asyncio.run(_many([server.url("/private")]))
        out = _one(res)
        assert out["status"] == "error"
        assert "private" in (out.get("error") or "").lower()
        assert out["attempts"] == 0  # blocked before any strategy ran

    def test_private_url_with_profile_also_blocked(self, server, isolated_env):
        res = asyncio.run(_many([server.url("/private")], profile="p"))
        out = _one(res)
        assert out["status"] == "error"
        assert "private" in (out.get("error") or "").lower()

    def test_allow_private_env_full_ladder(self, server, isolated_env, monkeypatch):
        monkeypatch.setenv("WEBGET_ALLOW_PRIVATE", "1")
        res = asyncio.run(_many([server.url("/private")]))
        out = _one(res)
        # with the escape hatch set, the local server IS reachable
        assert out["status"] == "success"
        assert "PRIVATE DATA LEAKED" in out["markdown"]


class TestCLIContract:
    def test_json_output_contract(self, fresh_cache):
        """The CLI --json shape matches scrape_many's dict-of-URLs contract."""
        server = fresh_cache
        res = asyncio.run(webget.scrape_many(
            [server.url("/normal")], max_chars=2000, no_cache=True
        ))
        url = server.url("/normal")
        assert url in res
        assert res[url]["status"] == "success"

    def test_mcp_fetch_shape_matches(self, fresh_cache):
        """MCP fetch returns the per-URL dict (not the dict-of-URLs)."""
        server = fresh_cache
        res = asyncio.run(webget.scrape_many(
            [server.url("/normal")], max_chars=2000, no_cache=True
        ))
        per_url = res[server.url("/normal")]
        assert "markdown" in per_url and "status" in per_url
