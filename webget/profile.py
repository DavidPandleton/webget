"""Persistent browser profile (auth session) management for webget.

Profiles live at ~/.local/share/webget/profiles/<name> by default
(override with WEBGET_PROFILE_DIR). They store Playwright storage state
(cookies + local storage) so authenticated fetches via the HTTP fast
path can reuse a session captured once in a real browser.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from urllib.parse import urlparse


def _warn(msg):
    """Print a warning to stderr so stdout (JSON) stays clean."""
    print(f"webget: warning: {msg}", file=sys.stderr)


# Order: env override first, then a monkeypatched/overridden PROFILE_DIR
# module attribute (existing tests patch it directly), then the default.
# NOTE: PROFILE_DIR is exposed as a module attr so tests can monkeypatch
# it; the value is resolved lazily via _profile_root() each call so
# patches take effect immediately.
PROFILE_DIR = os.path.expanduser("~/.local/share/webget/profiles")
_PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _profile_root():
    """Profile root dir; WEBGET_PROFILE_DIR env override for tests/ops.

    Order: env override first, then a monkeypatched/overridden PROFILE_DIR
    module attribute (existing tests patch it directly), then the default.
    """
    env = os.environ.get("WEBGET_PROFILE_DIR")
    if env:
        return env
    # Tests patch `webget_cli.PROFILE_DIR` (the shim module attribute).
    # Consult the shim so those patches take effect here too.
    try:
        import webget_cli as _shim

        patched = getattr(_shim, "PROFILE_DIR", None)
        if patched:
            return patched
    except ImportError:
        pass
    patched = globals().get("PROFILE_DIR")
    if patched:
        return patched
    return os.path.expanduser("~/.local/share/webget/profiles")


def profile_dir(name):
    """Return profile dir, rejecting path traversal / weird names."""
    if not name or not _PROFILE_NAME_RE.match(name) or name in (".", ".."):
        raise SystemExit(f"invalid profile name: {name!r}")
    return os.path.join(_profile_root(), name)


def profile_state_path(profile):
    return os.path.join(profile_dir(profile), "storage_state.json")


def load_profile_cookies(profile):
    """Load cookies from a profile's exported Playwright storage state."""
    p = profile_state_path(profile)
    if not os.path.exists(p):
        return None
    try:
        with open(p) as f:
            state = json.load(f)
        return state.get("cookies") or []
    except (json.JSONDecodeError, OSError):
        return None


def _auth_state(result, profile):
    """Classify auth state from observable signals. Returns (state, authenticated)."""
    md = (result.get("markdown") or "").lower()
    html = (result.get("html") or "").lower()
    status = result.get("status_code")
    text = f"{md} {html}"

    # Challenge markers (Cloudflare, CAPTCHA, etc.) - highest priority.
    challenge_markers = (
        "just a moment",
        "cf-chl",
        "challenge-platform",
        "captcha",
        "hcaptcha",
        "recaptcha",
        "verify you are human",
        "unusual traffic",
        "attention required",
        "cf-error-details",
    )
    if any(m in text for m in challenge_markers):
        return "challenge", None

    # Login page / form detection.
    has_password_input = "<input" in html and 'type="password"' in html
    login_words = any(w in text for w in ("log in", "login", "sign in", "signin"))
    # Sites like SION use JS show/hide instead of type=password and label
    # fields as "NIM" / "Username". Catch credential-labeled forms too.
    has_credential_labels = "password" in text and any(
        w in text for w in ("nim", "username", "user id", "email")
    )
    has_show_password = "show password" in text
    if status == 401:
        return "login_required", False
    if has_password_input and login_words:
        return "login_required", False
    if has_credential_labels or has_show_password:
        return "login_required", False
    if status == 403:
        # 403 + login/session markers -> login_required; generic 403 -> blocked
        if login_words or "session" in text or "expired" in text:
            return "login_required", False
        return "blocked", None
    if status == 429 or any(w in text for w in ("access denied", "blocked", "forbidden")):
        return "blocked", None

    if status and status >= 400:
        return "error", None
    # Success. authenticated is only meaningful when a profile session was used.
    return "success", (True if profile else None)


def _effective_cookies(cookies, profile):
    """Explicit --cookies win; otherwise load session cookies from profile."""
    if cookies is not None:
        return cookies
    if not profile:
        return None
    return load_profile_cookies(profile)


# ---------- Session management UX ----------


def _valid_site_url(raw):
    """Validate a user-supplied site URL. Returns (ok, hostname_or_error)."""
    if not raw or raw.isspace():
        return False, "no site URL given"
    if "://" not in raw:
        raw = "https://" + raw
    u = urlparse(raw)
    if u.scheme not in ("http", "https"):
        return False, f"unsupported scheme: {u.scheme or 'none'}"
    if not u.hostname:
        return False, f"could not parse hostname from {raw!r}"
    host = u.hostname.lower()
    # Reject spaces / anything that is not a plausible hostname or IP.
    if any(ch.isspace() for ch in host):
        return False, f"invalid hostname: {host!r}"
    if not all(ch.isalnum() or ch in ".-_" for ch in host):
        return False, f"invalid hostname: {host!r}"
    return True, host


def _profile_meta(profile):
    """Non-sensitive metadata about one profile. Never returns cookie values."""
    d = profile_dir(profile)
    state_p = profile_state_path(profile)
    last_used = None
    status = "unknown"
    size = 0
    for dirpath, _dirnames, filenames in os.walk(d):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                size += os.path.getsize(fp)
            except OSError:
                pass
            mtime = os.path.getmtime(fp)
            if last_used is None or mtime > last_used:
                last_used = mtime
    if os.path.exists(state_p):
        try:
            with open(state_p) as f:
                state = json.load(f)
            cookies = state.get("cookies") or []
            if cookies:
                now = time.time()
                # Treat cookie as live if it has no expiry or expires in the future.
                live = [
                    c
                    for c in cookies
                    if (c.get("expires") or -1) < 0 or (c.get("expires") or 0) > now
                ]
                status = "authenticated" if live else "expired"
        except (json.JSONDecodeError, OSError):
            status = "corrupt"
    return {"profile": profile, "last_used": last_used, "size": size, "status": status}


def _fmt_age(ts):
    if ts is None:
        return "-"
    diff = time.time() - ts
    if diff < 60:
        return f"{int(diff)}s ago"
    if diff < 3600:
        return f"{int(diff // 60)}m ago"
    if diff < 86400:
        return f"{int(diff // 3600)}h ago"
    return f"{int(diff // 86400)}d ago"


def list_profiles():
    """Non-sensitive metadata for every profile dir. Never cookie values."""
    if not os.path.isdir(_profile_root()):
        return []
    out = []
    for name in sorted(os.listdir(_profile_root())):
        if not os.path.isdir(profile_dir(name)):
            continue
        try:
            profile_dir(name)  # validates; raises SystemExit on bad names
            meta = _profile_meta(name)
            if meta["size"] > 0 or meta["status"] != "unknown":
                out.append(meta)
        except SystemExit:
            # Skip malformed dir names that fail validation (e.g. "..").
            continue
    return out


def profile_exists(name):
    """True if name is a valid profile name AND its dir exists."""
    try:
        return os.path.isdir(profile_dir(name))
    except SystemExit:
        return False


async def _login_flow(
    site,
    profile,
    headless,
    wait_seconds=120,
    interactive=True,
    quiet=False,
):
    """Login flow for a profile: open browser, user logs in, session persists.

    interactive=True (CLI): waits for Enter on stdin, exactly like before.
    interactive=False (MCP): stdin is the JSON-RPC stream, so a blocking
    input() would swallow protocol bytes. Instead we poll the browser
    context until a session cookie appears (the Set-Cookie side effect of
    the login handshake) or wait_seconds elapses, then persist.
    quiet=True (MCP): status lines go to stderr; stdout belongs to FastMCP.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise RuntimeError(
            "login requires playwright (not installed). Install with: "
            "uv tool install webget-cli --with webget-cli[browser] "
            "&& playwright install chromium"
        ) from None

    state_p = profile_state_path(profile)
    os.makedirs(profile_dir(profile), exist_ok=True)

    def _say(msg):
        if quiet:
            print(msg, file=sys.stderr)
        else:
            print(msg)

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=profile_dir(profile),
            headless=headless,
        )
        page = context.pages[0] if context.pages else await context.new_page()
        _say(f"Opening {site} in a browser with profile '{profile}'...")
        if interactive:
            _say("Log in manually in the browser window.")
            _say("When you are done, come back here and press Enter.")
        try:
            await page.goto(site, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:  # noqa: BLE001 - navigation issues shouldn't kill login
            _warn(f"could not navigate to {site}: {e}")
        if interactive:
            try:
                await asyncio.to_thread(input, "Press Enter when done: ")
            except EOFError:
                pass  # non-interactive stdin (tests) - proceed immediately
        else:
            await _wait_for_session_cookies(context, wait_seconds)
        try:
            await context.storage_state(path=state_p)
            _say(f"Session persisted for profile '{profile}'.")
        except Exception as e:  # noqa: BLE001 - persistence must surface
            _warn(f"failed to persist profile session for '{profile}': {e}")
        await context.close()


async def _wait_for_session_cookies(context, wait_seconds):
    """Poll the live browser context until a session cookie appears.

    Used by non-interactive login (MCP tool): there is no Enter keypress on
    the JSON-RPC stdin, so instead we watch for the login handshake's
    Set-Cookie side effect. We specifically look for a session cookie (no
    Expires/Max-Age attribute) so we don't exit early on incidental
    third-party trackers or pre-existing cookies that load with the page.
    Returns as soon as a session cookie appears, or after wait_seconds
    (in which case we persist anyway as a best-effort fallback).
    """
    deadline = time.monotonic() + max(0, wait_seconds)
    while time.monotonic() < deadline:
        try:
            cookies = await context.cookies()
            for c in cookies:
                # Session cookies have expires == -1 (or the field absent).
                # Persistent cookies carry a positive epoch timestamp.
                if c.get("expires", -1) <= 0:
                    return
        except Exception:  # noqa: BLE001, S110 - context mid-navigation; keep polling
            pass
        await asyncio.sleep(1)
    _warn(f"no session cookies observed within {wait_seconds}s; persisting anyway")


def _domain_match(cookie_domain, host):
    """True if cookie_domain (e.g. '.example.com') covers host."""
    d = (cookie_domain or "").lstrip(".").lower()
    h = host.lower()
    return d == h or h.endswith("." + d)


def _cookie_belongs_to(cookie_domain, host):
    """True if cookie_domain is host itself or a subdomain of host.

    Used for logout pruning: logging out 'campus.example' must also clear
    cookies from '.api.campus.example' (subdomains), while keeping
    'github.com' untouched.
    """
    d = (cookie_domain or "").lstrip(".").lower()
    h = host.lower()
    return d == h or d.endswith("." + h)


def _logout_domain_regex(host):
    """Regex matching host plus subdomains for Playwright clear_cookies.

    Matches 'campus.example', '.campus.example', 'api.campus.example',
    '.api.campus.example' but never 'github.com' or 'notevil.com'.
    """
    import re

    return re.compile(r"^(\.)?([^.]+\.)*" + re.escape(host) + r"$")


def _prune_storage_cookies(state, host):
    """Remove cookies belonging to host (and its subdomains) from a
    storage_state dict. Returns (new_state, removed_count)."""
    cookies = state.get("cookies") or []
    kept = [c for c in cookies if not _cookie_belongs_to(c.get("domain", ""), host)]
    state["cookies"] = kept
    return state, len(cookies) - len(kept)


async def _logout_flow(site, profile):
    from playwright.async_api import async_playwright

    ok, host = _valid_site_url(site)
    if not ok:
        return False, host
    state_p = profile_state_path(profile)
    removed = 0
    # 1. Prune the exported storage state (used by the HTTP fast path).
    if os.path.exists(state_p):
        try:
            from .search import _read_json, _write_json

            state = await asyncio.to_thread(_read_json, state_p)
            state, removed = _prune_storage_cookies(state, host)
            await asyncio.to_thread(_write_json, state_p, state)
        except (json.JSONDecodeError, OSError) as e:
            return False, f"could not read profile storage state: {e}"
    # 2. Clear the live browser context for that domain (persistent profile).
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=profile_dir(profile), headless=True
        )
        try:
            # Read cookies BEFORE clearing so we can report accurately.
            all_cookies = await context.cookies()
            before = len(all_cookies)
            # Playwright's clear_cookies accepts a regex domain filter. We
            # match host itself plus subdomains (leading-dot cookies like
            # '.campus.example' and '.api.campus.example'), while never
            # touching unrelated domains. No global clear: session cookies
            # survive because we never remove them.
            await context.clear_cookies(domain=_logout_domain_regex(host))
            after_cookies = await context.cookies()
            removed += before - len(after_cookies)
        except Exception as e:  # noqa: BLE001 - warn, still done
            _warn(f"could not clear browser cookies for {host}: {e}")
        await context.close()
    return True, removed
