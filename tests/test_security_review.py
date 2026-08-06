"""Phase 11 security review tests: SSRF representation coverage, redirect
hop behavior, allow-private escape hatch, ladder strategy invariance."""
import asyncio

import pytest

import webget_cli as webget


async def _fetch(url, **kw):
    return await webget.scrape_many([url], no_cache=True, **kw)


def _one(res):
    return res[next(iter(res))]


class TestSSRFRepresentations:
    """Every IP representation the guard must reject (literal + resolved)."""

    @pytest.mark.parametrize(
        "url",
        [
            # IPv4 literals
            "http://127.0.0.1/x",
            "http://127.255.255.255/x",
            "http://10.0.0.1/x",
            "http://10.255.255.255/x",
            "http://172.16.0.1/x",
            "http://172.31.255.255/x",
            "http://192.168.0.1/x",
            "http://192.168.255.255/x",
            "http://169.254.169.254/latest/meta-data/",
            "http://0.0.0.0/x",
            # IPv6 literals
            "http://[::1]/x",
            "http://[::ffff:127.0.0.1]/x",
            "http://[fc00::1]/x",
            "http://[fd00::1]/x",
            "http://[fe80::1]/x",
            "http://[::]/x",
            "http://[2001:db8::1]/x",  # documentation range (reserved)
            # hostnames
            "http://localhost/x",
            "http://localhost.localdomain/x",
            # URL with userinfo (must not bypass)
            "http://user:pass@127.0.0.1/x",
            "http://user:pass@10.0.0.1/x",
            # alternate numeric representations (resolved via getaddrinfo)
            "http://127.1/x",
            "http://2130706433/x",  # 127.0.0.1 as decimal
            "http://0x7f000001/x",  # 127.0.0.1 as hex
            # trailing dot variant of private literal
            "http://127.0.0.1./x",
        ],
    )
    def test_private_detected(self, url):
        assert webget._is_private_target(url) is True, f"should block {url}"

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/x",
            "http://example.com:8080/x",
            "https://github.com/",
            "https://1.1.1.1/x",  # public IP literal
            "https://8.8.8.8/x",
            "https://[2606:4700:4700::1111]/x",  # public IPv6
        ],
    )
    def test_public_allowed(self, url):
        assert webget._is_private_target(url) is False, f"should allow {url}"


class TestRedirectHopPolicy:
    def test_public_to_public_ok(self, server, fresh_cache):
        res = asyncio.run(_fetch(server.url("/redirect")))
        assert _one(res)["status"] == "success"

    def test_public_to_private_blocked(self, server, isolated_env, monkeypatch):
        """Simulate a PUBLIC initial URL that redirects to a private one:
        hop check must fire even though the initial URL passed."""
        import pytest

        real = webget._is_private_target
        initial = server.url("/redirect-private")

        def guarded(url):
            return real(url) if url != initial else False

        monkeypatch.setattr(webget, "_is_private_target", guarded)
        with pytest.raises(webget.SSRFError):
            asyncio.run(webget.fetch_http(initial, 2000, timeout=5))

    def test_multi_hop_into_private_blocked(self, server, isolated_env, monkeypatch):
        """public -> public -> private (3 hops) must still be blocked."""

        real = webget._is_private_target
        initial = server.url("/redirect-chain?n=2")  # -> /redirect-chain?n=1 -> /normal
        # craft: first two hops pass (public), third hop lands on /private
        chain = [server.url("/redirect-chain?n=2"), server.url("/redirect-chain?n=1")]

        def guarded(url):
            if url in chain or url == server.url("/redirect-chain?n=2"):
                return False
            return real(url)

        monkeypatch.setattr(webget, "_is_private_target", guarded)
        # fetch_http follows hops manually; every hop re-checks
        try:
            asyncio.run(webget.fetch_http(initial, 2000, timeout=5))
        except webget.SSRFError:
            return  # blocked at the private hop: correct
        except Exception as e:
            raise AssertionError(f"expected SSRFError at private hop, got {e!r}") from e
        # if it reached /normal without hitting a private hop, the chain didn't
        # point into /private, so this is fine too; the real hop test is above.
        raise AssertionError("chain unexpectedly never hit a private target")


class TestAllowPrivateEscapeHatch:
    def test_default_blocked(self, server, isolated_env):
        res = asyncio.run(_fetch(server.url("/private")))
        assert _one(res)["status"] == "error"
        assert "private" in (_one(res).get("error") or "").lower()

    def test_env_enables(self, server, isolated_env, monkeypatch):
        monkeypatch.setenv("WEBGET_ALLOW_PRIVATE", "1")
        res = asyncio.run(_fetch(server.url("/private")))
        assert _one(res)["status"] == "success"

    def test_env_parsing_strict(self, server, isolated_env, monkeypatch):
        """Only exact '1' enables. 'true', 'yes', '' must NOT enable."""
        for val in ("true", "TRUE", "yes", "on", "", "0", "2"):
            monkeypatch.setenv("WEBGET_ALLOW_PRIVATE", val)
            res = asyncio.run(_fetch(server.url("/private")))
            assert _one(res)["status"] == "error", f"value {val!r} must not enable"

    def test_override_does_not_disable_other_guards(self, server, isolated_env, monkeypatch):
        """allow-private only relaxes SSRF; invalid strategy still errors, etc."""
        monkeypatch.setenv("WEBGET_ALLOW_PRIVATE", "1")
        res = asyncio.run(_fetch("not a url"))
        assert _one(res)["status"] in ("error", "blocked")

    def test_parse_func_accepts_explicit_arg(self):
        assert webget._is_private_target("http://127.0.0.1/x", allow_private=True) is False
        assert webget._is_private_target("http://127.0.0.1/x", allow_private=False) is True


class TestLadderInvariant:
    """A URL blocked by SSRF policy must be blocked regardless of strategy."""

    @pytest.mark.parametrize("strategy", ["auto", "http", "crawl4ai", "firecrawl"])
    def test_blocked_for_all_strategies(self, server, isolated_env, strategy):
        url = server.url("/private")
        if strategy == "firecrawl":
            # _ladder raises SystemExit without a key; still must not fetch
            try:
                res = asyncio.run(_fetch(url, strategy=strategy))
            except SystemExit:
                return  # no key: ladder refuses before any network I/O
            assert _one(res)["status"] == "error"
        else:
            res = asyncio.run(_fetch(url, strategy=strategy))
            out = _one(res)
            assert out["status"] == "error"
            assert "private" in (out.get("error") or "").lower()

    def test_profile_does_not_bypass_ssrf(self, server, isolated_env):
        """--profile must not change the SSRF verdict."""
        res = asyncio.run(_fetch(server.url("/private"), profile="anyprofile"))
        out = _one(res)
        assert out["status"] == "error"
        assert "private" in (out.get("error") or "").lower()

    def test_cache_does_not_bypass_ssrf(self, server, isolated_env):
        """Even a cached private entry must not be served."""
        url = server.url("/private")
        # try to poison: direct cache_put of a 'success' for this URL
        webget.cache_put(url, None, None, 2000, {"status": "success", "markdown": "x" * 200}, None)
        res = asyncio.run(webget.scrape_many([url], max_chars=2000))
        out = res[url]
        assert out["status"] == "error"
        assert "private" in (out.get("error") or "").lower()


class TestSSRFDNSBehavior:
    def test_unknown_hostname_not_falsely_flagged(self):
        # unresolvable hostname: must not be treated as private (fetch will
        # fail on its own with a normal DNS error)
        assert webget._is_private_target("http://nonexistent-host-webget.invalid/x") is False
