"""Adversarial HTTP tests: URL handling, status codes, redirects, bodies."""

import asyncio

import pytest

import webget_cli as webget


async def _fetch(url, **kw):
    return await webget.scrape_many([url], no_cache=True, **kw)


def _one(res):
    return res[next(iter(res))]


# ---------- URL handling ----------


class TestInvalidURLs:
    @pytest.mark.parametrize(
        "bad",
        [
            "not a url",
            "://:",
            "",
            "ftp://example.com/file",
            "file:///etc/passwd",
            "data:text/html,hello",
            "javascript:alert(1)",
            "http://",
            "http://exa mple.com",
        ],
    )
    def test_invalid_urls_do_not_crash(self, bad):
        res = asyncio.run(_fetch(bad))
        out = _one(res)
        assert out["status"] in ("error", "blocked")
        assert "method" in out

    def test_no_scheme_added_by_httpx(self):
        # httpx refuses scheme-less URLs; ladder must record it as error.
        res = asyncio.run(_fetch("example.com"))
        assert _one(res)["status"] == "error"


class TestConnectionFailures:
    def test_connection_refused(self):
        res = asyncio.run(_fetch("http://127.0.0.1:1/", per_url_timeout=5))
        out = _one(res)
        assert out["status"] == "error"
        assert "method" in out  # any method is fine; must not crash

    def test_dns_failure(self):
        res = asyncio.run(
            _fetch("http://nonexistent-host-webget-audit.invalid/", per_url_timeout=8)
        )
        assert _one(res)["status"] == "error"

    def test_timeout(self, fresh_cache):
        server = fresh_cache
        res = asyncio.run(_fetch(server.url("/timeout?sec=5"), per_url_timeout=1))
        assert _one(res)["status"] == "error"


# ---------- status codes ----------


class TestStatusCodes:
    @pytest.mark.parametrize("path,expected", [("/401", "login_required"), ("/403", "blocked")])
    def test_auth_statuses(self, fresh_cache, path, expected):
        server = fresh_cache
        res = asyncio.run(_fetch(server.url(path)))
        assert _one(res)["status"] == expected

    @pytest.mark.parametrize("path", ["/404", "/500", "/429"])
    def test_error_statuses(self, fresh_cache, path):
        server = fresh_cache
        res = asyncio.run(_fetch(server.url(path)))
        assert _one(res)["status"] in ("error", "blocked")  # 429 -> blocked

    def test_403_login_words_login_required(self, fresh_cache):
        server = fresh_cache
        res = asyncio.run(_fetch(server.url("/403-login")))
        assert _one(res)["status"] == "login_required"


# ---------- redirects ----------


class TestRedirects:
    @pytest.mark.parametrize(
        "path", ["/redirect", "/redirect-301", "/redirect-307", "/redirect-308"]
    )
    def test_redirects_followed(self, fresh_cache, path):
        server = fresh_cache
        res = asyncio.run(_fetch(server.url(path)))
        out = _one(res)
        assert out["status"] == "success", out.get("error")

    def test_redirect_chain(self, fresh_cache):
        server = fresh_cache
        res = asyncio.run(_fetch(server.url("/redirect-chain?n=5")))
        assert _one(res)["status"] == "success"

    def test_redirect_loop_errors(self, fresh_cache):
        server = fresh_cache
        res = asyncio.run(_fetch(server.url("/redirect-loop")))
        out = _one(res)
        # httpx raises TooManyRedirects after default max; must be error, not hang.
        assert out["status"] == "error"
        assert "redirect" in (out.get("error") or "").lower() or out["status"] == "error"


# ---------- response bodies ----------


class TestResponseBodies:
    def test_empty_response(self, fresh_cache):
        server = fresh_cache
        res = asyncio.run(_fetch(server.url("/empty")))
        # empty body -> markdown empty -> "content too thin" -> error terminal
        assert _one(res)["status"] in ("error", "blocked")

    def test_malformed_html(self, fresh_cache):
        server = fresh_cache
        res = asyncio.run(_fetch(server.url("/malformed")))
        # markdownify usually still extracts something; at minimum no crash
        assert "status" in _one(res)

    def test_binary_response(self, fresh_cache):
        server = fresh_cache
        res = asyncio.run(_fetch(server.url("/binary")))
        assert _one(res)["status"] in ("error", "blocked", "success")

    def test_json_response(self, fresh_cache):
        server = fresh_cache
        res = asyncio.run(_fetch(server.url("/json")))
        assert _one(res)["status"] in ("error", "blocked", "success")

    def test_gzip_response(self, fresh_cache):
        server = fresh_cache
        res = asyncio.run(_fetch(server.url("/gzip")))
        assert _one(res)["status"] == "success"  # httpx auto-decompresses

    def test_huge_response_is_bounded(self, fresh_cache):
        server = fresh_cache
        # 5MB body; scrape_many must truncate, not blow memory or hang.
        res = asyncio.run(_fetch(server.url("/huge"), max_chars=1000))
        out = _one(res)
        assert out["status"] == "success"
        assert len(out["markdown"]) <= 1000
