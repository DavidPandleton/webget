"""Browser-strategy SSRF hop check (requires crawl4ai + chromium).

The HTTP fast path checks every redirect hop manually. The Crawl4AI
browser path must NOT be able to reach a private target through
- top-level navigation redirects, or
- SUBRESOURCE redirects (<img src=...> that 302s into loopback).

The local test server lives on 127.0.0.1, so the SSRF policy is
monkeypatched to treat ONLY the page + redirect endpoint as "public"
(initial and subresource start URLs). Every landing target inside
loopback is still checked by the REAL policy.

Every test asserts on the SERVER-SIDE hit counter for the private
endpoint: a hit count of 0 proves the private endpoint never received a
request at all, not merely that its response was discarded.
"""

import asyncio
import gzip
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

pytest.importorskip("crawl4ai", reason="crawl4ai not installed")

from http_server import TestServer

import webget_cli as webget

IMG_PAGE = "/public-with-img"  # <img src=/redirect-to-private-page>
IMG_PAGE_SECRET = "/public-with-img-secret"  # <img src=/redirect-to-secret>
REDIR_PAGE = "/redirect-to-private-page"  # 302 -> /private-page
REDIR_SECRET = "/redirect-to-secret"  # 302 -> /secret
PRIVATE_PAGE = "/private-page"  # loopback-only landing
SECRET = "/secret"  # loopback-only landing (counter)
NAV_BAIT = "/redirect-private-page"  # top-level navigation bait


@pytest.fixture()
def server():
    srv = TestServer().start()
    yield srv
    srv.stop()


@pytest.fixture()
def isolated(server, tmp_path, monkeypatch):
    monkeypatch.delenv("WEBGET_ALLOW_PRIVATE", raising=False)
    monkeypatch.setattr(webget, "PROFILE_DIR", str(tmp_path / "profiles"))
    monkeypatch.setattr(webget, "CACHE_DIR", str(tmp_path / "cache"))
    return server


def _fake_public_policy(server, extra_public=()):
    """SSRF policy that treats only the given paths' URLs as public and
    applies the REAL policy to everything else (the loopback landings)."""
    public_urls = {server.url(p) for p in extra_public}
    real = webget._is_private_target

    def guarded(url):
        if url in public_urls:
            return False
        return real(url)

    return guarded


async def _browser_fetch(url, **kw):
    return await webget.scrape_many([url], max_chars=4000, no_cache=True, strategy="crawl4ai", **kw)


class TestSubresourceRedirectSSRF:
    def test_subresource_redirect_to_private_page_blocked(self, isolated, monkeypatch):
        """<img src> 302 -> 127.0.0.1/private-page must never be fetched."""
        server = isolated
        monkeypatch.setattr(
            webget,
            "_is_private_target",
            _fake_public_policy(server, extra_public=(IMG_PAGE, REDIR_PAGE)),
        )
        server.reset_counters()
        res = asyncio.run(_browser_fetch(server.url(IMG_PAGE)))
        out = res[server.url(IMG_PAGE)]
        assert out["status"] == "success"  # public page itself is fine
        # The private landing page must NEVER have received a request.
        assert server.hits_for(PRIVATE_PAGE) == 0, (
            f"private endpoint received {server.hits_for(PRIVATE_PAGE)} requests"
        )

    def test_subresource_redirect_to_secret_blocked(self, isolated, monkeypatch):
        """Same with a second landing path: proves counter is per-path."""
        server = isolated
        monkeypatch.setattr(
            webget,
            "_is_private_target",
            _fake_public_policy(server, extra_public=(IMG_PAGE_SECRET, REDIR_SECRET)),
        )
        server.reset_counters()
        res = asyncio.run(_browser_fetch(server.url(IMG_PAGE_SECRET)))
        out = res[server.url(IMG_PAGE_SECRET)]
        assert out["status"] == "success"
        assert server.hits_for(SECRET) == 0, (
            f"secret endpoint received {server.hits_for(SECRET)} requests"
        )

    def test_subresource_redirect_hop_never_reached(self, isolated, monkeypatch):
        """The 302 landing URL is checked BEFORE fetching: even a single
        request to the private host must not happen. Total request count
        stays small (page + its own assets only)."""
        server = isolated
        monkeypatch.setattr(
            webget,
            "_is_private_target",
            _fake_public_policy(server, extra_public=(IMG_PAGE, REDIR_PAGE)),
        )
        server.reset_counters()
        asyncio.run(_browser_fetch(server.url(IMG_PAGE)))
        assert server.hits_for(PRIVATE_PAGE) == 0
        # The redirect endpoint itself is fetched once (it is "public" by
        # the test policy) and returns 302; the hop is then blocked.
        assert server.hits_for(REDIR_PAGE) == 1


class TestNavigationRedirectSSRF:
    def test_top_level_redirect_into_private_blocked(self, isolated, monkeypatch):
        server = isolated
        monkeypatch.setattr(
            webget,
            "_is_private_target",
            _fake_public_policy(server, extra_public=(NAV_BAIT,)),
        )
        server.reset_counters()
        res = asyncio.run(_browser_fetch(server.url(NAV_BAIT)))
        out = res[server.url(NAV_BAIT)]
        assert out["status"] == "error", "top-level redirect into private leaked"
        assert server.hits_for(PRIVATE_PAGE) == 0, "private page WAS fetched"

    def test_direct_private_url_blocked_before_browser(self, isolated):
        server = isolated
        server.reset_counters()
        res = asyncio.run(_browser_fetch(server.url(PRIVATE_PAGE)))
        out = res[server.url(PRIVATE_PAGE)]
        assert out["status"] == "error"
        # scrape_many's pre-check blocks before the browser ever runs.
        assert server.hits_for(PRIVATE_PAGE) == 0


class TestBinaryPostGuard:
    """Regression (0.7.1 bug): the route guard read request.post_data which
    decodes UTF-8 and raises UnicodeDecodeError on gzip/binary POST bodies
    (seen on facebook/linkedin). The handler died BEFORE the private-address
    check, Playwright continued the request natively, and a binary POST to a
    private target BYPASSED the SSRF guard entirely.

    Fix: guard reads undecoded bytes (post_data_buffer); SSRF decisions are
    URL/IP policy only. These tests prove:
      - binary POST to PUBLIC target: guard alive, exact body replayed
      - binary POST to PRIVATE target: aborted, endpoint NEVER receives it
    """

    BINARY_BODY = gzip.compress(b"binary payload " * 50)

    def _driver_url(self, server, target):
        return server.url(f"/binary-post-driver?to={target}")

    def test_binary_post_to_public_allowed(self, isolated, monkeypatch):
        server = isolated
        driver = "/binary-post-driver?to=/binary-post"
        monkeypatch.setattr(
            webget,
            "_is_private_target",
            _fake_public_policy(server, extra_public=(driver, "/binary-post")),
        )
        server.reset_counters()
        res = asyncio.run(_browser_fetch(server.url(driver)))
        out = res[server.url(driver)]
        assert out["status"] == "success", out
        # The gzip POST went through the guard; the server received the
        # EXACT bytes (guard replayed them via route.fetch, no crash).
        assert server.hits_for("/binary-post") == 1
        assert server.last_body("/binary-post") == self.BINARY_BODY

    def test_binary_post_to_private_blocked(self, isolated, monkeypatch):
        server = isolated
        driver = "/binary-post-driver?to=/binary-post-private"
        monkeypatch.setattr(
            webget,
            "_is_private_target",
            _fake_public_policy(server, extra_public=(driver,)),
        )
        server.reset_counters()
        res = asyncio.run(_browser_fetch(server.url(driver)))
        out = res[server.url(driver)]
        assert out["status"] == "success", out  # driver page itself is fine
        # The private sink must NEVER receive the binary POST: with the
        # 0.7.1 bug the guard crashed before the private check and the
        # request leaked through (native continue). Zero hits = no bypass.
        assert server.hits_for("/binary-post-private") == 0, (
            f"private binary POST leaked {server.hits_for('/binary-post-private')} request(s)"
        )
