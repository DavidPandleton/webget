import os
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import webget  # noqa: E402


def opts(*args):
    """Run parse_opts and return its tuple."""
    return webget.parse_opts(list(args))


def auth_state(md="", html="", status=None, profile=None):
    return webget._auth_state(
        {"markdown": md, "html": html, "status_code": status}, profile
    )


# ---------- parse_opts ----------

class TestParseOpts:
    def test_positional(self):
        remaining, *_, limit, strategy, profile, no_cache = opts("u", "https://x.com")
        assert remaining == ["u", "https://x.com"]
        assert limit is None and strategy == "auto" and profile is None
        assert no_cache is False

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
        *_, limit, _, _, _ = opts("s", "q", "--limit", "7")
        assert limit == 7

    def test_profile_and_no_cache(self):
        *_, profile, no_cache = opts("u", "https://x.com", "--profile", "campus")
        assert profile == "campus" and no_cache is False
        *_, profile, no_cache = opts("u", "https://x.com", "--no-cache")
        assert profile is None and no_cache is True

    def test_strategy(self):
        *_, strategy, _, _ = opts("u", "https://x.com", "--strategy", "crawl4ai")
        assert strategy == "crawl4ai"

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
        state, authed = auth_state(md="lots of content here", html="<html>..</html>", status=200, profile=None)
        assert state == "success" and authed is None

    def test_success_with_profile(self):
        state, authed = auth_state(md="content", status=200, profile="campus")
        assert state == "success" and authed is True

    def test_401_login_required(self):
        state, authed = auth_state(status=401, profile="campus")
        assert state == "login_required" and authed is False

    def test_login_form_detected(self):
        html = '<html><form><input type="text"/><input type="password"/></form><h1>Log in</h1></html>'
        state, authed = auth_state(html=html, status=200)
        assert state == "login_required"

    def test_challenge_markers(self):
        for marker in ("Just a moment", "cf-chl-opt", "captcha", "hcaptcha",
                       "Verify you are human", "unusual traffic"):
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
        state, authed, detail = webget._terminal_state(reasons, None)
        assert state == "challenge"

    def test_priority_login_over_blocked(self):
        reasons = [("blocked", "http", "403"), ("login_required", "crawl4ai", "401")]
        state, authed, _ = webget._terminal_state(reasons, None)
        assert state == "login_required" and authed is False

    def test_empty_reasons(self):
        state, authed, detail = webget._terminal_state([], None)
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
