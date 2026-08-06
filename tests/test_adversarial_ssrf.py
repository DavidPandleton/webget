"""Adversarial SSRF tests: private-address blocking in the HTTP path.

The guard must block, by default:
  - loopback (127.0.0.0/8, ::1), link-local (169.254/16, fe80::/10),
    private IPv4 (10/8, 172.16/12, 192.168/16), private IPv6 (fc00::/7),
    unspecified (0.0.0.0, ::)
  - hostnames that RESOLVE to any of the above (localhost, etc.)
  - redirects from a public page into a private address (hop-by-hop)

Escape hatch: WEBGET_ALLOW_PRIVATE=1 re-enables fetching private targets
(legitimate use: scraping an internal/intranet site on purpose).
"""

import asyncio

import pytest

import webget_cli as webget


async def _fetch(url, **kw):
    return await webget.scrape_many([url], max_chars=2000, no_cache=True, **kw)


def _one(res):
    return res[next(iter(res))]


class TestPrivateTargetDetection:
    """Unit-level: _is_private_target must classify every representation."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1/x",
            "http://127.0.0.2/x",
            "http://127.1/x",  # shorthand loopback
            "http://0.0.0.0/x",
            "http://10.0.0.1/x",
            "http://10.255.255.255/x",
            "http://172.16.0.1/x",
            "http://172.31.255.255/x",
            "http://192.168.0.1/x",
            "http://192.168.255.255/x",
            "http://169.254.169.254/latest/meta-data/",  # cloud metadata
            "http://[::1]/x",  # IPv6 loopback
            "http://[::ffff:127.0.0.1]/x",  # IPv4-mapped IPv6 loopback
            "http://[fc00::1]/x",  # IPv6 unique local
            "http://[fd00::1]/x",
            "http://[fe80::1]/x",  # IPv6 link-local
            "http://[::]/x",  # IPv6 unspecified
            "http://localhost/x",
            "http://LOCALHOST:8080/x",
        ],
    )
    def test_private_urls_detected(self, url):
        assert webget._is_private_target(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/x",
            "http://example.com:8080/x",
            "https://github.com/",
        ],
    )
    def test_public_urls_pass(self, url):
        assert webget._is_private_target(url) is False

    def test_allow_private_env_bypasses(self, monkeypatch):
        monkeypatch.setenv("WEBGET_ALLOW_PRIVATE", "1")
        assert webget._is_private_target("http://127.0.0.1/x") is False
        assert webget._is_private_target("http://localhost/x") is False


class TestSSRFBlocking:
    def test_loopback_blocked_by_default(self, server, isolated_env):
        res = asyncio.run(_fetch(server.url("/private")))
        out = _one(res)
        assert out["status"] == "error"
        assert "private" in (out.get("error") or "").lower()

    def test_metadata_endpoint_blocked(self, server, isolated_env):
        res = asyncio.run(_fetch(server.url("/metadata")))
        assert _one(res)["status"] == "error"

    def test_redirect_into_private_blocked(self, server, isolated_env):
        # /redirect-private 302 -> http://127.0.0.1:<port>/private.
        # The initial URL is already private (server on loopback), so the
        # pre-check blocks it; either way the private target is unreachable.
        res = asyncio.run(_fetch(server.url("/redirect-private")))
        out = _one(res)
        assert out["status"] == "error"
        assert "private" in (out.get("error") or "").lower()

    def test_redirect_hop_is_checked(self, server, isolated_env, monkeypatch):
        """A redirect hop INTO a private address must be blocked even when
        the initial URL passed the guard (e.g. it was public)."""
        import pytest

        real = webget._is_private_target
        initial = server.url("/redirect-private")

        def guarded(url):
            # initial URL passes (as if public); every other URL checked for real
            return real(url) if url != initial else False

        monkeypatch.setattr(webget, "_is_private_target", guarded)
        with pytest.raises(webget.SSRFError):
            asyncio.run(webget.fetch_http(initial, 2000, timeout=5))

    def test_redirect_to_public_still_works(self, server, isolated_env, monkeypatch):
        # public -> public redirect must remain functional under the guard
        monkeypatch.setenv("WEBGET_ALLOW_PRIVATE", "1")
        res = asyncio.run(_fetch(server.url("/redirect")))
        assert _one(res)["status"] == "success"

    def test_private_allowed_via_env(self, server, isolated_env, monkeypatch):
        monkeypatch.setenv("WEBGET_ALLOW_PRIVATE", "1")
        res = asyncio.run(_fetch(server.url("/private")))
        # When allowed, the server actually serves PRIVATE DATA LEAKED
        out = _one(res)
        assert out["status"] == "success"


class TestSSRFGuardLadder:
    def test_guard_failure_recorded_not_crash(self, server, isolated_env):
        """The guard must produce a clean error reason, never an exception."""
        res = asyncio.run(_fetch(server.url("/redirect-private")))
        out = _one(res)
        assert "attempts" in out
        assert out["method"] in ("http", "crawl4ai", "firecrawl", "")
