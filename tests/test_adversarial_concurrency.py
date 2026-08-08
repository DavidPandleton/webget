"""Adversarial concurrency tests: bounded parallelism, duplicate URLs,
shared-state races in scrape_many."""

import asyncio

import webget_cli as webget


async def _fetch_many(urls, **kw):
    return await webget.scrape_many(urls, max_chars=2000, no_cache=True, **kw)


class TestBoundedConcurrency:
    def test_http_concurrency_is_bounded(self, server, fresh_cache, monkeypatch):
        """100 unique URLs against /concurrency must never exceed the cap."""
        urls = [server.url(f"/concurrency?i={i}") for i in range(100)]
        server.reset_counters()
        res = asyncio.run(_fetch_many(urls, per_url_timeout=10))
        assert len(res) == 100
        cap = webget._DEFAULT_CONCURRENCY
        assert server.max_active <= cap, f"observed {server.max_active} concurrent, cap {cap}"

    def test_custom_concurrency_cap(self, server, fresh_cache, monkeypatch):
        """max_concurrency=3 must be honored."""
        urls = [server.url(f"/concurrency?i={i}") for i in range(30)]
        server.reset_counters()
        res = asyncio.run(_fetch_many(urls, per_url_timeout=10, max_concurrency=3))
        assert len(res) == 30
        assert server.max_active <= 3, f"observed {server.max_active}, cap 3"


class TestDuplicateURLs:
    def test_duplicate_urls_fetched_once(self, server, fresh_cache):
        """[u, u] must produce ONE attempt per URL, not duplicate work."""
        url = server.url("/normal")
        res = asyncio.run(_fetch_many([url, url]))
        out = res[url]
        assert out["attempts"] == 1
        assert len(res) == 1  # deduped key


class TestBatchScaling:
    def test_batch_200_mixed(self, server, fresh_cache):
        """Mixed batch (success, login, blocked, error) all complete."""
        paths = ["/normal", "/login", "/403", "/429", "/thin", "/redirect"]
        urls = [server.url(p) for p in paths] * 34  # 204 URLs
        res = asyncio.run(_fetch_many(urls, per_url_timeout=10))
        assert len(res) == len(set(urls))
        statuses = {v["status"] for v in res.values()}
        assert "success" in statuses
        assert {"login_required", "blocked", "error"} & statuses

    def test_empty_batch_returns_empty(self):
        assert asyncio.run(_fetch_many([])) == {}
