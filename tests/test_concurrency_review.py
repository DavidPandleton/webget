"""Phase 11 concurrency + resource lifecycle review tests."""

import asyncio

import pytest

import webget_cli as webget


async def _many(urls, **kw):
    return await webget.scrape_many(urls, no_cache=True, **kw)


class TestConcurrencyInvalidValues:
    """max_concurrency edge values must not crash or deadlock."""

    @pytest.mark.parametrize("bad", [0, -1, -100])
    def test_nonpositive_rejected_or_safe(self, fresh_cache, bad):
        server = fresh_cache
        urls = [server.url(f"/normal?i={i}") for i in range(5)]
        # Semaphore(0) would deadlock; Semaphore(-1) raises ValueError.
        # Either behavior is acceptable as long as it does not hang.
        try:
            res = asyncio.run(_many(urls, max_concurrency=bad, per_url_timeout=5))
            assert len(res) == 5  # if it ran, all answered
        except (TimeoutError, ValueError):
            pass

    def test_huge_value_is_safe(self, fresh_cache):
        server = fresh_cache
        urls = [server.url(f"/normal?i={i}") for i in range(20)]
        res = asyncio.run(_many(urls, max_concurrency=100000, per_url_timeout=10))
        assert len(res) == 20

    def test_concurrency_bounds_observed(self, server, fresh_cache):
        urls = [server.url(f"/concurrency?i={i}") for i in range(60)]
        server.reset_counters()
        asyncio.run(_many(urls, max_concurrency=5, per_url_timeout=10))
        assert server.max_active <= 5


class TestWorkerFailureLifecycle:
    def test_exception_in_worker_does_not_leak_semaphore(self, server, fresh_cache):
        """A failing URL must release the semaphore so others still run."""
        urls = [server.url("/404") for _ in range(3)] + [server.url("/normal?ok=1")]
        res = asyncio.run(_many(urls, per_url_timeout=10))
        assert res[server.url("/normal?ok=1")]["status"] == "success"
        assert res[server.url("/404")]["status"] in ("error", "blocked")

    def test_timeout_does_not_leak_semaphore(self, server, fresh_cache):
        urls = [server.url("/timeout?sec=5")] * 2 + [server.url("/normal?ok=1")]
        res = asyncio.run(_many(urls, per_url_timeout=1, max_concurrency=2))
        assert res[server.url("/normal?ok=1")]["status"] == "success"

    def test_no_pending_tasks_after_batch(self, fresh_cache):
        server = fresh_cache
        urls = [server.url(f"/normal?i={i}") for i in range(20)]

        async def run():
            await _many(urls, per_url_timeout=10)
            me = asyncio.current_task()
            return [t for t in asyncio.all_tasks() if not t.done() and t is not me]

        pending = asyncio.run(run())
        assert pending == []


class TestCancellation:
    def test_cancel_batch_does_not_hang(self, fresh_cache):
        server = fresh_cache
        urls = [server.url(f"/slow?sec=1&i={i}") for i in range(10)]

        async def run():
            task = asyncio.create_task(_many(urls, per_url_timeout=10))
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(asyncio.wait_for(run(), timeout=10))
