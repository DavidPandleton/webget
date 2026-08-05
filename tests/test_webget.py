import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import webget


def opts(*args):
    """Run parse_opts and return its tuple."""
    return webget.parse_opts(list(args))


def auth_state(md="", html="", status=None, profile=None):
    return webget._auth_state({"markdown": md, "html": html, "status_code": status}, profile)


# ---------- parse_opts ----------


class TestParseOpts:
    def test_positional(self):
        remaining, *_, limit, strategy, profile, no_cache, headless = opts("u", "https://x.com")
        assert remaining == ["u", "https://x.com"]
        assert limit is None and strategy == "auto" and profile is None
        assert no_cache is False and headless is False

    def test_cookies_short_and_long(self, tmp_path):
        ck = tmp_path / "ck.txt"
        ck.write_text("# Netscape HTTP Cookie File\n.x.com\tTRUE\t/\tFALSE\t0\ts\tv\n")
        r1, c1, *_ = opts("-c", str(ck), "u", "https://x.com")
        assert r1 == ["u", "https://x.com"]
        assert c1 is not None and c1[0]["name"] == "s"
        r2, c2, *_ = opts("--cookies", str(ck), "u", "https://x.com")
        assert r2 == ["u", "https://x.com"]
        assert c2 is not None

    def test_max_chars_aliases(self):
        _, _, _, mc1, *_ = opts("-n", "500", "u", "https://x.com")
        _, _, _, mc2, *_ = opts("--max-chars", "500", "u", "https://x.com")
        assert mc1 == 500 and mc2 == 500

    def test_limit(self):
        *_, limit, _, _, _, _ = opts("s", "q", "--limit", "7")
        assert limit == 7

    def test_profile_and_no_cache(self):
        *_, profile, no_cache, _ = opts("u", "https://x.com", "--profile", "campus")
        assert profile == "campus" and no_cache is False
        *_, profile, no_cache, _ = opts("u", "https://x.com", "--no-cache")
        assert profile is None and no_cache is True

    def test_strategy(self):
        *_, strategy, _, _, _ = opts("u", "https://x.com", "--strategy", "crawl4ai")
        assert strategy == "crawl4ai"

    def test_headless(self):
        *_, headless = opts("login", "https://x.com", "--headless")
        assert headless is True

    def test_unknown_flag_passthrough(self):
        remaining, *_ = opts("u", "https://x.com", "--weird")
        assert "--weird" in remaining


# ---------- profile safety ----------


class TestProfileSafety:
    def test_valid_names(self):
        for name in ("campus", "campus-2026", "a.b_c", "x1"):
            assert webget.profile_dir(name).startswith(webget.PROFILE_DIR)

    def test_path_traversal_rejected(self):
        for name in ("../../etc", "..", ".", "a/b", "a\\b", "~evil", "", "/abs"):
            try:
                webget.profile_dir(name)
                assert False, f"should reject {name!r}"
            except SystemExit:
                pass

    def test_profile_state_path_safe(self):
        p = webget.profile_state_path("campus")
        assert p.endswith("campus/storage_state.json")


# ---------- auth state classifier ----------


class TestAuthState:
    def test_success_public(self):
        state, authed = auth_state(
            md="lots of content here", html="<html>..</html>", status=200, profile=None
        )
        assert state == "success" and authed is None

    def test_success_with_profile(self):
        state, authed = auth_state(md="content", status=200, profile="campus")
        assert state == "success" and authed is True

    def test_401_login_required(self):
        state, authed = auth_state(status=401, profile="campus")
        assert state == "login_required" and authed is False

    def test_login_form_detected(self):
        html = (
            '<html><form><input type="text"/><input type="password"/></form><h1>Log in</h1></html>'
        )
        state, _authed = auth_state(html=html, status=200)
        assert state == "login_required"

    def test_challenge_markers(self):
        for marker in (
            "Just a moment",
            "cf-chl-opt",
            "captcha",
            "hcaptcha",
            "Verify you are human",
            "unusual traffic",
        ):
            state, _ = auth_state(md=marker, status=200)
            assert state == "challenge", f"{marker!r} should be challenge"

    def test_403_generic_blocked(self):
        state, _ = auth_state(status=403, md="Forbidden")
        assert state == "blocked"

    def test_403_with_login_words_login_required(self):
        state, _ = auth_state(status=403, md="Please log in to continue")
        assert state == "login_required"

    def test_429_blocked(self):
        state, _ = auth_state(status=429)
        assert state == "blocked"

    def test_access_denied_blocked(self):
        state, _ = auth_state(md="Access denied", status=200)
        assert state == "blocked"


# ---------- terminal state picker ----------


class TestTerminalState:
    def test_priority_challenge_over_login(self):
        reasons = [("login_required", "http", "login"), ("challenge", "crawl4ai", "captcha")]
        state, _authed, _detail = webget._terminal_state(reasons, None)
        assert state == "challenge"

    def test_priority_login_over_blocked(self):
        reasons = [("blocked", "http", "403"), ("login_required", "crawl4ai", "401")]
        state, authed, _ = webget._terminal_state(reasons, None)
        assert state == "login_required" and authed is False

    def test_empty_reasons(self):
        state, _authed, _detail = webget._terminal_state([], None)
        assert state == "error"

    def test_error_fallback(self):
        reasons = [("error", "http", "timeout"), ("error", "crawl4ai", "boom")]
        state, _, detail = webget._terminal_state(reasons, None)
        assert state == "error" and "timeout" in detail


# ---------- ladder ----------


class TestLadder:
    def test_auto_includes_http_and_crawl4ai(self):
        steps = webget._ladder("auto", key="")
        assert "http" in steps and "crawl4ai" in steps
        assert "firecrawl" not in steps  # no key -> skipped

    def test_auto_with_key_includes_firecrawl(self):
        steps = webget._ladder("auto", key="abc")
        assert "firecrawl" in steps

    def test_http_only(self):
        assert webget._ladder("http", "") == ["http"]

    def test_firecrawl_without_key_fails(self):
        try:
            webget._ladder("firecrawl", "")
            assert False
        except SystemExit:
            pass

    def test_unknown_strategy_fails(self):
        try:
            webget._ladder("banana", "")
            assert False
        except SystemExit:
            pass


# ---------- cache isolation ----------


class TestCacheIsolation:
    def test_public_vs_profile(self):
        p1 = webget._cache_path("https://x.com", None, None, 1000, None)
        p2 = webget._cache_path("https://x.com", None, None, 1000, "campus")
        p3 = webget._cache_path("https://x.com", None, None, 1000, "work")
        assert len({p1, p2, p3}) == 3

    def test_strategy_not_in_key(self):
        # cache is content-level: same url+chars+profile -> same key regardless of method
        a = webget._cache_path("https://x.com", None, None, 500, None)
        b = webget._cache_path("https://x.com", None, None, 500, None)
        assert a == b

    def test_max_chars_in_key(self):
        a = webget._cache_path("https://x.com", None, None, 500, None)
        b = webget._cache_path("https://x.com", None, None, 900, None)
        assert a != b

    def test_cookies_in_key(self):
        ck = [{"name": "s", "value": "v1", "domain": "x.com"}]
        a = webget._cache_path("https://x.com", None, None, 500, None)
        b = webget._cache_path("https://x.com", ck, None, 500, None)
        assert a != b


# ---------- Phase 4: session management ----------


class TestValidSiteURL:
    def test_plain_domain(self):
        ok, host = webget._valid_site_url("example.com")
        assert ok and host == "example.com"

    def test_full_url(self):
        ok, host = webget._valid_site_url("https://campus.example/dashboard")
        assert ok and host == "campus.example"

    def test_no_scheme_added(self):
        ok, host = webget._valid_site_url("campus.example")
        assert ok and host == "campus.example"

    def test_bad_scheme(self):
        ok, _err = webget._valid_site_url("ftp://x.com")
        assert not ok and "scheme" in _err

    def test_empty(self):
        ok, _err = webget._valid_site_url("")
        assert not ok

    def test_garbage(self):
        ok, _err = webget._valid_site_url("://:")
        assert not ok

    def test_spaces_rejected(self):
        ok, _err = webget._valid_site_url("not a url")
        assert not ok


class TestDomainMatch:
    def test_exact(self):
        assert webget._domain_match("example.com", "example.com")

    def test_leading_dot(self):
        assert webget._domain_match(".example.com", "example.com")

    def test_subdomain(self):
        assert webget._domain_match(".example.com", "api.example.com")

    def test_unrelated(self):
        assert not webget._domain_match("other.com", "example.com")

    def test_suffix_trap(self):
        # evil.com must not match notevil.com
        assert not webget._domain_match("evil.com", "notevil.com")


class TestProfileMeta:
    def test_unknown_without_state(self, tmp_path, monkeypatch):
        monkeypatch.setattr(webget, "PROFILE_DIR", str(tmp_path))
        d = tmp_path / "campus"
        d.mkdir()
        meta = webget._profile_meta("campus")
        assert meta["status"] == "unknown"

    def test_authenticated_with_live_cookie(self, tmp_path, monkeypatch):
        monkeypatch.setattr(webget, "PROFILE_DIR", str(tmp_path))
        d = tmp_path / "campus"
        (d / "Default").mkdir(parents=True)
        state = {
            "cookies": [{"name": "s", "value": "secret", "domain": ".example.com", "expires": -1}]
        }
        import json

        (d / "storage_state.json").write_text(json.dumps(state))
        meta = webget._profile_meta("campus")
        assert meta["status"] == "authenticated"

    def test_expired_cookie(self, tmp_path, monkeypatch):
        monkeypatch.setattr(webget, "PROFILE_DIR", str(tmp_path))
        d = tmp_path / "campus"
        d.mkdir()
        state = {"cookies": [{"name": "s", "value": "x", "domain": ".e.com", "expires": 1}]}
        import json

        (d / "storage_state.json").write_text(json.dumps(state))
        meta = webget._profile_meta("campus")
        assert meta["status"] == "expired"

    def test_corrupt_state(self, tmp_path, monkeypatch):
        monkeypatch.setattr(webget, "PROFILE_DIR", str(tmp_path))
        d = tmp_path / "campus"
        d.mkdir()
        (d / "storage_state.json").write_text("{not json")
        meta = webget._profile_meta("campus")
        assert meta["status"] == "corrupt"

    def test_meta_never_contains_cookie_values(self, tmp_path, monkeypatch):
        monkeypatch.setattr(webget, "PROFILE_DIR", str(tmp_path))
        d = tmp_path / "campus"
        (d / "Default").mkdir(parents=True)
        state = {"cookies": [{"name": "session", "value": "SUPERSECRET", "domain": ".e.com"}]}
        import json

        (d / "storage_state.json").write_text(json.dumps(state))
        blob = json.dumps(webget._profile_meta("campus"))
        assert "SUPERSECRET" not in blob
        assert "session" not in blob


class TestLogoutPrune:
    def _state(self):
        return {
            "cookies": [
                {"name": "a", "value": "1", "domain": ".campus.example"},
                {"name": "b", "value": "2", "domain": "github.com"},
                {"name": "c", "value": "3", "domain": ".other.example"},
            ]
        }

    def test_prune_one_domain(self):
        state = self._state()
        cookies = state["cookies"]
        kept = [
            c for c in cookies if not webget._domain_match(c.get("domain", ""), "campus.example")
        ]
        assert len(kept) == 2
        assert all(c["domain"] != ".campus.example" for c in kept)

    def test_preserve_other_domains(self):
        state = self._state()
        kept = [
            c
            for c in state["cookies"]
            if not webget._domain_match(c.get("domain", ""), "campus.example")
        ]
        assert {c["domain"] for c in kept} == {"github.com", ".other.example"}

    def test_all_domains_kept_when_no_match(self):
        state = self._state()
        kept = [
            c for c in state["cookies"] if not webget._domain_match(c.get("domain", ""), "nope.com")
        ]
        assert len(kept) == 3
