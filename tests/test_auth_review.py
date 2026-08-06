"""Phase 11 auth/profile + SSRF interaction review."""
import asyncio
import json
import os

import pytest

import webget_cli as webget


async def _fetch(url, **kw):
    return await webget.scrape_many([url], no_cache=True, **kw)


def _cookie(name, domain, expires=-1):
    return {"name": name, "value": "v", "domain": domain, "path": "/", "secure": False, "expires": expires}


def _seed_profile(name, cookies, isolated_env):
    p = webget.profile_state_path(name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        json.dump({"cookies": cookies}, f)


class TestProfileSSRFInteraction:
    def test_profile_cannot_bypass_ssrf(self, server, isolated_env):
        """Authenticated profile + private URL: STILL blocked."""
        _seed_profile("auth", [_cookie("session", server.host)], isolated_env)
        res = asyncio.run(_fetch(server.url("/private"), profile="auth"))
        out = res[server.url("/private")]
        assert out["status"] == "error"
        assert "private" in (out.get("error") or "").lower()

    def test_profile_cannot_bypass_ssrf_redirect(self, server, isolated_env):
        _seed_profile("auth", [_cookie("session", server.host)], isolated_env)
        res = asyncio.run(_fetch(server.url("/redirect-private"), profile="auth"))
        out = res[server.url("/redirect-private")]
        assert out["status"] == "error"

    def test_auth_public_url_works(self, fresh_cache, isolated_env):
        """Authenticated profile + PUBLIC url: session cookie is sent."""
        server = fresh_cache
        _seed_profile("auth", [_cookie("session", server.host)], isolated_env)
        res = asyncio.run(_fetch(server.url("/cookie-gated"), profile="auth"))
        out = res[server.url("/cookie-gated")]
        assert out["status"] == "success"

    def test_missing_profile_falls_back_anonymous(self, fresh_cache):
        server = fresh_cache
        res = asyncio.run(_fetch(server.url("/normal"), profile="ghost"))
        out = res[server.url("/normal")]
        assert out["status"] == "success"
        assert out["auth"]["profile"] == "ghost"

    def test_expired_session_detected(self, fresh_cache, isolated_env):
        server = fresh_cache
        _seed_profile("old", [_cookie("session", server.host, expires=1000000000)], isolated_env)
        res = asyncio.run(_fetch(server.url("/cookie-gated"), profile="old"))
        out = res[server.url("/cookie-gated")]
        assert out["status"] in ("blocked", "login_required")

    def test_corrupted_profile_no_crash(self, fresh_cache, isolated_env):
        server = fresh_cache
        p = webget.profile_state_path("corrupt")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write("{not json")
        res = asyncio.run(_fetch(server.url("/normal"), profile="corrupt"))
        assert res[server.url("/normal")]["status"] == "success"

    def test_logout_after_ssrf_failure(self, isolated_env):
        """SSRF failure must not corrupt profile state; logout still works."""
        _seed_profile("work", [_cookie("a", ".a.com"), _cookie("b", ".b.com")], isolated_env)
        loaded = webget.load_profile_cookies("work")
        assert loaded is not None
        state, removed = webget._prune_storage_cookies({"cookies": loaded}, "a.com")
        assert removed == 1
        assert {c["domain"] for c in state["cookies"]} == {".b.com"}


class TestProfileValidation:
    def test_invalid_profile_names_rejected(self):
        for name in ("../../etc", "a/b", "..", ".", ""):
            with pytest.raises(SystemExit):
                webget.profile_dir(name)

    def test_profile_name_not_url(self):
        # a profile name must not be treated as a URL or host
        assert webget._PROFILE_NAME_RE.match("campus-2026")
        assert not webget._PROFILE_NAME_RE.match("https://evil.com")


class TestAuthStateWithProfile:
    def test_success_with_profile_authenticated_true(self):
        state, authed = webget._auth_state(
            {"markdown": "x" * 200, "status_code": 200}, "campus"
        )
        assert state == "success" and authed is True

    def test_success_anonymous_authenticated_none(self):
        state, authed = webget._auth_state(
            {"markdown": "x" * 200, "status_code": 200}, None
        )
        assert state == "success" and authed is None
