"""Browser-strategy SSRF hop check (requires crawl4ai + chromium).

The HTTP fast path checks every redirect hop manually. The Crawl4AI
browser path must NOT be able to reach a private target through a
redirect from an allowed URL. These tests simulate a PUBLIC initial URL
that redirects into loopback (the test server lives on 127.0.0.1, so we
make the guard treat ONLY the initial URL as public — every other URL is
checked for real).
"""
import asyncio
import os
import tempfile

import pytest

crawl4ai = pytest.importorskip("crawl4ai", reason="requires [browser] extra")

import webget_cli as webget
from tests.http_server import TestServer


def _isolated():
    tmp = tempfile.mkdtemp(prefix="webget-browser-")
    webget.PROFILE_DIR = os.path.join(tmp, "profiles")
    webget.CACHE_DIR = os.path.join(tmp, "cache")
    os.makedirs(webget.PROFILE_DIR, exist_ok=True)
    os.makedirs(webget.CACHE_DIR, exist_ok=True)
    return tmp


def test_browser_redirect_into_private_must_not_leak(monkeypatch):
    """SIMULATED public URL -> 302 -> loopback /private-page.

    Guard policy: initial URL treated as public (monkeypatched), all other
    URLs checked for real. The browser must not deliver private content.
    """
    _isolated()
    monkeypatch.delenv("WEBGET_ALLOW_PRIVATE", raising=False)  # default policy
    srv = TestServer().start()
    try:
        initial = srv.url("/redirect-private-page")
        real = webget._is_private_target

        def guarded(url):
            # only the initial URL is "public"; redirect hops are real
            return real(url) if url != initial else False

        monkeypatch.setattr(webget, "_is_private_target", guarded)

        async def run():
            res = await webget.scrape_many(
                [initial], max_chars=4000, no_cache=True, strategy="crawl4ai"
            )
            return res[initial]

        out = asyncio.run(run())
        # With a hop guard this is an error; without one: success + leak.
        assert out["status"] != "success", (
            "browser strategy leaked private content via redirect: "
            f"{out.get('markdown', '')[:80]!r}"
        )
        assert "PRIVATE DATA LEAKED" not in out.get("markdown", "")
    finally:
        srv.stop()


def test_browser_direct_private_blocked_by_precheck(monkeypatch):
    """Direct private URL is blocked before the browser runs (pre-check)."""
    _isolated()
    monkeypatch.delenv("WEBGET_ALLOW_PRIVATE", raising=False)
    srv = TestServer().start()
    try:
        initial = srv.url("/private-page")

        async def run():
            res = await webget.scrape_many(
                [initial], max_chars=4000, no_cache=True, strategy="crawl4ai"
            )
            return res[initial]

        out = asyncio.run(run())
        assert out["status"] == "error"
        assert "private" in (out.get("error") or "").lower()
        assert "PRIVATE DATA LEAKED" not in out.get("markdown", "")
    finally:
        srv.stop()
