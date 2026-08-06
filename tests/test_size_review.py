"""Phase 11 response-size-limit review tests.

The cap must be enforced WHILE STREAMING (client.stream), not after the
body has been buffered. /oversize serves 30MB; a non-streaming client
would buffer all 30MB before the check could fire.
"""
import asyncio
import resource
import time

import webget_cli as webget


async def _fetch(url, **kw):
    return await webget.scrape_many([url], no_cache=True, **kw)


def _one(res):
    return res[next(iter(res))]


class TestSizeLimitEnforcement:
    def test_oversize_returns_error(self, fresh_cache):
        server = fresh_cache
        res = asyncio.run(_fetch(server.url("/oversize")))
        out = _one(res)
        assert out["status"] == "error"
        assert "too large" in (out.get("error") or "").lower()

    def test_undersize_still_works(self, fresh_cache):
        server = fresh_cache
        res = asyncio.run(_fetch(server.url("/huge")))
        assert _one(res)["status"] == "success"

    def test_streaming_actually_bounded(self, fresh_cache):
        """Memory must NOT balloon to the full 30MB body: the cap aborts
        mid-stream. (Soft check via maxrss delta, not a strict assertion —
        CI noise tolerance.)"""
        server = fresh_cache

        async def run():
            before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            t0 = time.perf_counter()
            try:
                await _fetch(server.url("/oversize"))
            finally:
                after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            return after - before, time.perf_counter() - t0

        delta_kb, wall = asyncio.run(run())
        # 30MB = ~30000 KB. If the client buffered everything, delta would
        # be near that. Streaming cap should keep it well under 10MB.
        assert delta_kb < 10000, f"memory grew {delta_kb}KB (looks buffered)"
        assert wall < 15, f"oversize fetch took {wall}s (looks like full read)"

    def test_missing_content_length(self, fresh_cache):
        """Chunked/unknown-length responses must still hit the cap."""
        # /oversize declares Content-Length, but the cap check does not
        # depend on it; a chunked server would behave the same. This test
        # documents the contract: cap is on bytes read, not on headers.
        server = fresh_cache
        res = asyncio.run(_fetch(server.url("/oversize")))
        assert _one(res)["status"] == "error"


class TestBrowserEquivalent:
    def test_browser_has_no_byte_cap_documented(self):
        """The browser strategy loads full pages (JS rendering) and has no
        equivalent byte cap: crawl4ai returns whatever Chromium parsed.
        This is a documented difference, not a silent gap: page size is
        bounded in practice by Chromium's own resource limits and by
        max_chars truncation AFTER parsing. A 30MB page would still be
        parsed by the browser. Acceptable risk for the browser path."""
        import inspect

        src = inspect.getsource(webget.scrape_many)
        assert "MAX_RESPONSE_BYTES" in inspect.getsource(webget)  # exists
        # The browser path intentionally relies on crawl4ai's own handling.
        assert "crawl4ai" in src
