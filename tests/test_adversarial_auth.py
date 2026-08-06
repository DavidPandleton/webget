"""Adversarial auth + cookie isolation tests: profiles, storage state,
logout domain scoping, session reuse, corrupted state."""
import asyncio
import json
import os

import pytest

import webget_cli as webget


async def _fetch(url, **kw):
    return await webget.scrape_many([url], max_chars=2000, no_cache=True, **kw)


def _one(res):
    return res[next(iter(res))]


def _cookie(name, domain, expires=-1):
    return {"name": name, "value": "v", "domain": domain, "path": "/", "secure": False, "expires": expires}


class TestProfileSafety:
    def test_profile_dir_rejects_traversal(self):
        for name in ("../../etc", "..", ".", "a/b", "a\\b", "~evil", "", "/abs"):
            with pytest.raises(SystemExit):
                webget.profile_dir(name)


class TestStorageState:
    def test_load_profile_cookies_missing_file(self, isolated_env):
        assert webget.load_profile_cookies("ghost") is None

    def test_load_profile_cookies_corrupt(self, isolated_env):
        p = webget.profile_state_path("campus")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write("{not json")
        assert webget.load_profile_cookies("campus") is None

    def test_load_profile_cookies_ok(self, isolated_env):
        p = webget.profile_state_path("campus")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            json.dump({"cookies": [_cookie("s", ".campus.example")]}, f)
        ck = webget.load_profile_cookies("campus")
        assert ck and ck[0]["name"] == "s"


class TestCookieDomainMatching:
    def test_belongs_to_subdomains(self):
        host = "campus.example"
        assert webget._cookie_belongs_to("campus.example", host)
        assert webget._cookie_belongs_to(".campus.example", host)
        assert webget._cookie_belongs_to(".api.campus.example", host)
        assert webget._cookie_belongs_to("api.campus.example", host)
        # not unrelated, not suffix-trap
        assert not webget._cookie_belongs_to("github.com", host)
        assert not webget._cookie_belongs_to("notevil.com", host)
        assert not webget._cookie_belongs_to("example.com.evil.com", host)

    def test_domain_match_public(self):
        assert webget._domain_match(".example.com", "api.example.com")
        assert not webget._domain_match(".example.com", "example.com.evil.com")


class TestLogoutScoping:
    def test_logout_prunes_only_target_domain(self, isolated_env):
        """Storage-state pruning: logout campus.example keeps github cookies."""
        state = {
            "cookies": [
                _cookie("campus", ".campus.example"),
                _cookie("api", ".api.campus.example"),
                _cookie("gh", ".github.com"),
            ]
        }
        new_state, removed = webget._prune_storage_cookies(state, "campus.example")
        assert removed == 2
        domains = {c["domain"] for c in new_state["cookies"]}
        assert ".campus.example" not in domains
        assert ".api.campus.example" not in domains
        assert ".github.com" in domains  # unrelated survives

    def test_logout_preserves_unrelated_domains_file(self, isolated_env):
        """End-to-end through _write_json: the file on disk is updated."""
        state = {"cookies": [_cookie("a", ".a.com"), _cookie("b", ".b.com")]}
        p = webget.profile_state_path("work")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            json.dump(state, f)
        with open(p) as f:
            loaded = json.load(f)
        new_state, removed = webget._prune_storage_cookies(loaded, "a.com")
        assert removed == 1
        domains = {c["domain"] for c in new_state["cookies"]}
        assert ".a.com" not in domains and ".b.com" in domains


class TestSessionReuse:
    def test_profile_cookies_sent_to_gated_page(self, fresh_cache, isolated_env):
        server = fresh_cache
        """Cookie-gated /cookie-gated returns 403 without a cookie, success with."""
        res = asyncio.run(_fetch(server.url("/cookie-gated")))
        assert _one(res)["status"] == "blocked"

        # seed a profile with a cookie for the server host
        host = server.host
        p = webget.profile_state_path("local")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            json.dump({"cookies": [_cookie("session", host)]}, f)

        res = asyncio.run(_fetch(server.url("/cookie-gated"), profile="local"))
        assert _one(res)["status"] == "success"

    def test_explicit_cookies_win_over_profile(self, fresh_cache, isolated_env):
        server = fresh_cache
        ck = [_cookie("session", server.host)]
        res = asyncio.run(_fetch(server.url("/cookie-gated"), cookies=ck))
        assert _one(res)["status"] == "success"


class TestProfileMeta:
    def test_corrupt_state_reports_corrupt(self, isolated_env):
        d = webget.profile_dir("broken")
        os.makedirs(d, exist_ok=True)
        with open(webget.profile_state_path("broken"), "w") as f:
            f.write("{{{{")
        assert webget._profile_meta("broken")["status"] == "corrupt"

    def test_expired_cookies_reported_expired(self, isolated_env):
        d = webget.profile_dir("old")
        os.makedirs(d, exist_ok=True)
        with open(webget.profile_state_path("old"), "w") as f:
            json.dump({"cookies": [_cookie("s", ".x.com", expires=1000000000)]}, f)
        assert webget._profile_meta("old")["status"] == "expired"
