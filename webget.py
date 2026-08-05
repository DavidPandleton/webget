#!/usr/bin/env -S uv run python3
"""webget - local search + scrape, zero API keys, unlimited usage.
Usage:
  webget s "query" [n]           Search via DuckDuckGo (default 5)
  webget u "https://..."         Scrape URL -> markdown (HTTP fast path, falls back)
  webget su "query" [n]          Search + scrape top n results (default 3, parallel)
  webget s "q" | webget u -      Pipe: pass URL from search via stdin
                                 (multi-line stdin = batch scrape)
  webget login URL --profile X   Open browser, log in manually, persist session
  webget profiles [--json]       List profiles and session status
  webget logout URL --profile X  Clear auth for one domain, keep the rest
Aliases: search = s, fetch = u, search-fetch = su

Options:
  -c, --cookies FILE  Netscape-format cookie file (like curl -b)
  --profile NAME      Persistent browser profile (auth session) in
                      ~/.local/share/webget/profiles/<name>
  -H, --header "K: V" Extra header (repeatable)
  -n, --max-chars N   Max output chars (default: 10000 for u, 4000 for su)
  --limit N           Result count for s/su (default: 5 / 3)
  -t, --timeout N     Per-URL timeout in seconds (default: 20)
  --fresh             Bypass cache and re-scrape
  --ttl N             Cache TTL in seconds (default: 3600)
  --strategy S        Fetch strategy: auto|http|crawl4ai|firecrawl (default auto)
  --no-cache          Don't read or write the disk cache (private fetch)
  --headless          Run login browser without a window (tests/automation)
  --json              Output results as JSON with metadata
                      (status/method/cached/auth)

Status values: success | login_required | challenge | blocked | error

Session management:
  webget login never stores passwords and never fills forms. You log in
  yourself in the opened browser window; webget just persists the session.
"""

import asyncio
import hashlib
import json
import os
import re
import sys
import time


def parse_cookie_file(path):
    """Parse Netscape cookie file -> Playwright-compatible cookie dicts."""
    cookies = []
    with open(os.path.expanduser(path)) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            domain, _domain_flag, path_, secure, expires, name, value = parts[:7]
            cookie = {
                "name": name,
                "value": value,
                "domain": domain,
                "path": path_,
                "secure": secure == "TRUE",
                "httpOnly": False,
            }
            if expires not in ("0", "Session", ""):
                try:
                    cookie["expires"] = int(expires)
                except ValueError:
                    pass
            cookies.append(cookie)
    return cookies


def parse_headers(raw_headers):
    """Parse list of 'Key: Value' strings into a dict."""
    headers = {}
    for h in raw_headers:
        if ":" in h:
            k, v = h.split(":", 1)
            headers[k.strip()] = v.strip()
    return headers


CACHE_DIR = os.path.expanduser("~/.cache/webget")


def _cache_path(url, cookies, headers, max_chars, profile=None):
    key = hashlib.sha1(
        f"{profile or 'public'}|{url}|{max_chars}|"
        f"{json.dumps(cookies or [], sort_keys=True)}|"
        f"{json.dumps(headers or {}, sort_keys=True)}".encode()
    ).hexdigest()
    return os.path.join(CACHE_DIR, key + ".json")


def cache_get(url, cookies, headers, max_chars, ttl, fresh=False, profile=None):
    if fresh:
        return None
    p = _cache_path(url, cookies, headers, max_chars, profile)
    if not os.path.exists(p):
        return None
    if time.time() - os.path.getmtime(p) > ttl:
        return None
    try:
        with open(p) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def cache_put(url, cookies, headers, max_chars, data, profile=None):
    os.makedirs(CACHE_DIR, exist_ok=True)
    p = _cache_path(url, cookies, headers, max_chars, profile)
    try:
        with open(p, "w") as f:
            json.dump({**data, "fetched_at": time.time()}, f)
        # simple eviction: keep newest 400 of 500
        try:
            files = [
                os.path.join(CACHE_DIR, f) for f in os.listdir(CACHE_DIR) if f.endswith(".json")
            ]
            if len(files) > 500:
                files.sort(key=os.path.getmtime)
                for f in files[:-400]:
                    os.remove(f)
        except OSError:
            pass
    except OSError:
        pass  # cache is best-effort


def parse_opts(args):
    """Extract options from positional args list."""
    cookies = None
    headers_list = []
    max_chars = None
    timeout = None
    fresh = False
    ttl = 3600
    json_out = False
    limit = None
    strategy = "auto"
    profile = None
    no_cache = False
    headless = False
    remaining = []
    i = 0
    while i < len(args):
        if args[i] in ("-c", "--cookies") and i + 1 < len(args):
            cookies = parse_cookie_file(args[i + 1])
            i += 2
        elif args[i] == "--profile" and i + 1 < len(args):
            profile = args[i + 1]
            i += 2
        elif args[i] == "--no-cache":
            no_cache = True
            i += 1
        elif args[i] == "--headless":
            headless = True
            i += 1
        elif args[i] in ("-H", "--header") and i + 1 < len(args):
            headers_list.append(args[i + 1])
            i += 2
        elif args[i] in ("-n", "--max-chars") and i + 1 < len(args):
            max_chars = int(args[i + 1])
            i += 2
        elif args[i] == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1])
            i += 2
        elif args[i] in ("-t", "--timeout") and i + 1 < len(args):
            timeout = int(args[i + 1])
            i += 2
        elif args[i] == "--fresh":
            fresh = True
            i += 1
        elif args[i] == "--ttl" and i + 1 < len(args):
            ttl = int(args[i + 1])
            i += 2
        elif args[i] == "--strategy" and i + 1 < len(args):
            strategy = args[i + 1]
            i += 2
        elif args[i] == "--json":
            json_out = True
            i += 1
        else:
            remaining.append(args[i])
            i += 1
    return (
        remaining,
        cookies,
        parse_headers(headers_list),
        max_chars,
        timeout,
        fresh,
        ttl,
        json_out,
        limit,
        strategy,
        profile,
        no_cache,
        headless,
    )


def _extract_markdown(html):
    """Try trafilatura (clean article text) then html2text (full markdown)."""
    try:
        import trafilatura

        text = trafilatura.extract(html, include_comments=False, include_tables=True)
        if text and len(text.strip()) > 100:
            return text.strip()
    except Exception:  # noqa: BLE001, S110 - extraction libs vary; fall through
        pass
    try:
        import html2text

        h = html2text.HTML2Text()
        h.ignore_links = False
        h.body_width = 0
        md = h.handle(html).strip()
        return md if len(md) > 50 else ""
    except Exception:  # noqa: BLE001 - best-effort extraction, empty is fine
        return ""


async def fetch_http(url, max_chars, cookies=None, headers=None, timeout=15):
    """Fast path: plain HTTP GET + local markdown extraction."""
    import httpx

    hdrs = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    }
    if headers:
        hdrs.update(headers)
    cj = {}
    if cookies:
        from urllib.parse import urlparse

        host = (urlparse(url).hostname or "").lower()
        if host:
            for c in cookies:
                d = (c.get("domain") or "").lstrip(".").lower()
                if d and (host == d or host.endswith("." + d)):
                    cj[c["name"]] = c["value"]
    async with httpx.AsyncClient(
        follow_redirects=True, timeout=timeout, headers=hdrs, cookies=cj
    ) as client:
        r = await client.get(url)
        ctype = r.headers.get("content-type", "")
        if "html" not in ctype and "text" not in ctype:
            raise RuntimeError(f"not HTML ({ctype or 'unknown'})")
        html = r.text
        title = ""
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
        if m:
            title = re.sub(r"\s+", " ", m.group(1)).strip()
        md = await asyncio.to_thread(_extract_markdown, html)
        return {
            "title": title,
            "markdown": md[:max_chars],
            "status_code": r.status_code,
            "html": html[:8000],
        }


def _warn(msg):
    """Print a warning to stderr so stdout (JSON) stays clean."""
    print(f"webget: warning: {msg}", file=sys.stderr)


def firecrawl_key():
    return os.environ.get("WEBGET_FIRECRAWL_KEY", "").strip()


PROFILE_DIR = os.path.expanduser("~/.local/share/webget/profiles")
_PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def profile_dir(name):
    """Return profile dir, rejecting path traversal / weird names."""
    if not name or not _PROFILE_NAME_RE.match(name) or name in (".", ".."):
        raise SystemExit(f"invalid profile name: {name!r}")
    return os.path.join(PROFILE_DIR, name)


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


async def fetch_firecrawl(url, max_chars, key, timeout=30):
    """Firecrawl escape hatch: POST /v1/scrape, formats markdown."""
    import httpx

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            "https://api.firecrawl.dev/v1/scrape",
            headers={"Authorization": f"Bearer {key}"},
            json={"url": url, "formats": ["markdown"]},
        )
        if r.status_code != 200:
            raise RuntimeError(f"Firecrawl HTTP {r.status_code}: {r.text[:200]}")
        data = r.json().get("data") or {}
        md = data.get("markdown", "") or ""
        if not md:
            raise RuntimeError("Firecrawl empty result")
        meta = data.get("metadata", {}) or {}
        return {
            "title": meta.get("title", ""),
            "markdown": md[:max_chars],
            "status_code": r.status_code,
            "html": "",
        }


def _ladder(strategy, key):
    steps = []
    if strategy in ("auto", "http"):
        steps.append("http")
    if strategy in ("auto", "crawl4ai"):
        steps.append("crawl4ai")
    if strategy in ("auto", "firecrawl") and key:
        steps.append("firecrawl")
    elif strategy == "firecrawl" and not key:
        raise SystemExit("WEBGET_FIRECRAWL_KEY not set")
    if not steps:
        raise SystemExit(f"unknown strategy: {strategy}")
    return steps


def _normalize_hit(hit):
    return {
        "title": hit.get("title", ""),
        "markdown": hit.get("markdown", ""),
        "status": "success",
        "method": "cache",
        "cached": True,
        "attempts": 1,
        "error": None,
        "auth": hit.get("auth") or {"profile": None, "authenticated": None, "state": "success"},
    }


async def _crawl4ai_once(crawler, cfg, url, per_url_timeout):
    async def attempt():
        task = asyncio.create_task(crawler.arun(url=url, config=cfg))
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        r = await asyncio.wait_for(task, timeout=per_url_timeout)
        if not getattr(r, "success", True):
            raise RuntimeError(getattr(r, "error_message", None) or "crawl failed")
        return r

    # Policy: retry once on timeout / transient failures; don't retry auth errors.
    def _no_retry(e):
        msg = str(e).lower()
        return any(
            m in msg for m in ("401", "403", "challenge", "captcha", "access denied", "forbidden")
        )

    try:
        r = await attempt()
        return {
            "title": (r.metadata or {}).get("title", ""),
            "markdown": (r.markdown or ""),
            "status_code": getattr(r, "status_code", None),
            "html": (r.html or "")[:8000],
        }
    except Exception as e:
        if _no_retry(e):
            raise
        await asyncio.sleep(2)
        try:
            r2 = await attempt()
            return {
                "title": (r2.metadata or {}).get("title", ""),
                "markdown": (r2.markdown or ""),
                "status_code": getattr(r2, "status_code", None),
                "html": (r2.html or "")[:8000],
            }
        except Exception:  # noqa: BLE001 - surface original error, retry already done
            raise RuntimeError(str(e)) from e


def _effective_cookies(cookies, profile):
    """Explicit --cookies win; otherwise load session cookies from profile."""
    if cookies is not None:
        return cookies
    if not profile:
        return None
    return load_profile_cookies(profile)


async def scrape_many(
    urls,
    max_chars=6000,
    per_url_timeout=20,
    cookies=None,
    headers=None,
    ttl=3600,
    fresh=False,
    strategy="auto",
    profile=None,
    no_cache=False,
):
    steps = _ladder(strategy, firecrawl_key())
    results = {}
    missing = []
    for u in urls:
        hit = None if no_cache else cache_get(u, cookies, headers, max_chars, ttl, fresh, profile)
        if hit:
            results[u] = _normalize_hit(hit)
        else:
            missing.append(u)
    if not missing:
        return results

    attempts = {u: 0 for u in missing}
    reasons = {u: [] for u in missing}
    pending = list(missing)

    async def record(url, method, res=None, exc=None):
        """Classify one strategy result; return success dict or None (keep climbing)."""
        attempts[url] += 1
        if exc is not None or res is None:
            reasons[url].append(("error", method, str(exc or "no result")))
            return None
        state, authenticated = _auth_state(res, profile)
        if state == "success" and len((res.get("markdown") or "").strip()) >= 100:
            auth = {"profile": profile, "authenticated": authenticated, "state": state}
            out = {
                "title": res.get("title", ""),
                "markdown": res.get("markdown", ""),
                "status": "success",
                "method": method,
                "cached": False,
                "attempts": attempts[url],
                "error": None,
                "auth": auth,
            }
            if not no_cache:
                cache_put(url, cookies, headers, max_chars, out, profile)
            return out
        if state == "success":
            reasons[url].append((state, method, "content too thin"))
        else:
            reasons[url].append((state, method, _auth_message(state, profile)))
        return None

    # Pass 1: HTTP fast path - no browser involved.
    if "http" in steps:

        async def http_one(url):
            try:
                res = await fetch_http(
                    url,
                    max_chars,
                    _effective_cookies(cookies, profile),
                    headers,
                    timeout=per_url_timeout,
                )
                return url, await record(url, "http", res=res)
            except TimeoutError:
                return url, await record(url, "http", exc=TimeoutError("timeout"))
            except Exception as e:  # noqa: BLE001 - record reason, ladder continues
                return url, await record(url, "http", exc=e)

        for url, out in await asyncio.gather(*(http_one(u) for u in pending)):
            if out:
                results[url] = out
        pending = [u for u in pending if u not in results]

    # Pass 2: Crawl4AI browser - only launched if something still needs it.
    if pending and "crawl4ai" in steps:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig

        bc = BrowserConfig(
            use_persistent_context=bool(profile),
            user_data_dir=profile_dir(profile) if profile else None,
            cookies=cookies or None,
            headers=headers or None,
        )
        cfg = CrawlerRunConfig()
        async with AsyncWebCrawler(config=bc, verbose=False) as crawler_ctx:

            async def crawl_one(url):
                try:
                    res = await _crawl4ai_once(crawler_ctx, cfg, url, per_url_timeout)
                    res["markdown"] = res.get("markdown", "")[:max_chars]
                    return url, await record(url, "crawl4ai", res=res)
                except TimeoutError:
                    return url, await record(url, "crawl4ai", exc=TimeoutError("timeout"))
                except Exception as e:  # noqa: BLE001 - record reason, ladder continues
                    return url, await record(url, "crawl4ai", exc=e)

            for url, out in await asyncio.gather(*(crawl_one(u) for u in pending)):
                if out:
                    results[url] = out
            pending = [u for u in pending if u not in results]
            if profile:
                try:
                    # Persist session so future HTTP-path fetches can reuse it.
                    # Note: crawl4ai's export_storage_state is broken in 0.9.2
                    # (accesses self.default_context on the strategy, which
                    # lives on browser_manager instead) - go direct.
                    bm = crawler_ctx.crawler_strategy.browser_manager
                    if bm and bm.default_context is not None:
                        await bm.default_context.storage_state(path=profile_state_path(profile))
                    else:
                        _warn(
                            f"profile '{profile}' used but no browser context "
                            "available; session will NOT be persisted"
                        )
                except Exception as e:  # noqa: BLE001 - warn, don't crash the batch
                    _warn(f"failed to persist profile session for '{profile}': {e}")

    # Pass 3: Firecrawl - optional cloud escape hatch.
    if pending and "firecrawl" in steps:

        async def fc_one(url):
            try:
                res = await fetch_firecrawl(
                    url, max_chars, firecrawl_key(), timeout=per_url_timeout
                )
                return url, await record(url, "firecrawl", res=res)
            except Exception as e:  # noqa: BLE001 - record reason, ladder continues
                return url, await record(url, "firecrawl", exc=e)

        for url, out in await asyncio.gather(*(fc_one(u) for u in pending)):
            if out:
                results[url] = out
        pending = [u for u in pending if u not in results]

    # Ladder exhausted for the rest: terminal state from all recorded reasons.
    for url in pending:
        state, authenticated, detail = _terminal_state(reasons[url], profile)
        auth = {"profile": profile, "authenticated": authenticated, "state": state}
        results[url] = {
            "title": "",
            "markdown": "",
            "status": state,
            "method": steps[-1],
            "cached": False,
            "attempts": attempts[url],
            "error": detail,
            "auth": auth,
        }
    return results


def _terminal_state(reasons, profile):
    """Pick final state from ladder reasons. Priority: challenge > login_required > blocked > error."""
    order = ("challenge", "login_required", "blocked", "error")
    for wanted in order:
        for state, method, detail in reasons:
            if state == wanted:
                authenticated = None
                if state == "login_required":
                    authenticated = False
                return state, authenticated, detail
    return "error", None, "; ".join(d for _, _, d in reasons)


def _auth_message(state, profile):
    if state == "login_required":
        return f"Authenticated session appears expired or unavailable (profile={profile or 'none'})"
    if state == "challenge":
        return "Site requires human verification; automatic acquisition stopped"
    if state == "blocked":
        return "Access blocked by the site (403/429 or access-denied page)"
    return "Unknown fetch failure"


def search(query, n=5):
    from ddgs import DDGS

    return [
        {"title": r["title"], "url": r["href"], "snippet": r.get("body", "")}
        for r in DDGS().text(query, max_results=n)
    ]


def _read_json(path):
    """Sync helper for to_thread: read + parse JSON, raises on bad data."""
    with open(path) as f:
        return json.load(f)


def _write_json(path, data):
    """Sync helper for to_thread: write JSON."""
    with open(path, "w") as f:
        json.dump(data, f)


# ---------- Phase 4: session management UX ----------


def _valid_site_url(raw):
    """Validate a user-supplied site URL. Returns (ok, hostname_or_error)."""
    from urllib.parse import urlparse

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


def cmd_profiles(json_out):
    if not os.path.isdir(PROFILE_DIR):
        if json_out:
            print("{}")
        else:
            print("PROFILE     LAST USED     SIZE       STATUS\n(no profiles yet)")
        return
    profiles = []
    for name in sorted(os.listdir(PROFILE_DIR)):
        if not os.path.isdir(profile_dir(name)):
            continue
        try:
            profile_dir(name)  # validates name; raises SystemExit on bad names
            meta = _profile_meta(name)
            if meta["size"] > 0 or meta["status"] != "unknown":
                profiles.append(meta)
        except SystemExit:
            # Skip malformed dir names that fail validation (e.g. "..").
            continue
    if json_out:
        print(json.dumps({p["profile"]: p for p in profiles}, indent=2))
        return
    print(f"{'PROFILE':<12} {'LAST USED':<12} {'SIZE':>10}  STATUS")
    for p in profiles:
        print(
            f"{p['profile']:<12} {_fmt_age(p['last_used']):<12} "
            f"{p['size'] / 1024 / 1024:>8.1f} MB  {p['status']}"
        )


async def _login_flow(site, profile, headless):
    from playwright.async_api import async_playwright

    state_p = profile_state_path(profile)
    os.makedirs(profile_dir(profile), exist_ok=True)

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=profile_dir(profile),
            headless=headless,
        )
        page = context.pages[0] if context.pages else await context.new_page()
        print(f"Opening {site} in a browser with profile '{profile}'...")
        print("Log in manually in the browser window.")
        print("When you are done, come back here and press Enter.")
        try:
            await page.goto(site, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:  # noqa: BLE001 - navigation issues shouldn't kill login
            _warn(f"could not navigate to {site}: {e}")
        try:
            await asyncio.to_thread(input, "Press Enter when done: ")
        except EOFError:
            pass  # non-interactive stdin (tests) - proceed immediately
        try:
            await context.storage_state(path=state_p)
            print(f"Session persisted for profile '{profile}'.")
        except Exception as e:  # noqa: BLE001 - persistence must surface
            _warn(f"failed to persist profile session for '{profile}': {e}")
        await context.close()


def cmd_login(site, profile, headless):
    ok, host = _valid_site_url(site)
    if not ok:
        print(f"error: invalid site URL: {host}")
        return 2
    if not profile:
        print("error: --profile NAME is required for login")
        return 2
    try:
        profile_dir(profile)
    except SystemExit as e:
        print(f"error: {e}")
        return 2
    print(f"webget login: profile '{profile}' for {host}")
    asyncio.run(_login_flow(site, profile, headless))
    return 0


def _domain_match(cookie_domain, host):
    """True if cookie_domain (e.g. '.example.com') covers host."""
    d = (cookie_domain or "").lstrip(".").lower()
    h = host.lower()
    return d == h or h.endswith("." + d)


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
            state = await asyncio.to_thread(_read_json, state_p)
            cookies = state.get("cookies") or []
            kept = [c for c in cookies if not _domain_match(c.get("domain", ""), host)]
            removed = len(cookies) - len(kept)
            state["cookies"] = kept
            await asyncio.to_thread(_write_json, state_p, state)
        except (json.JSONDecodeError, OSError) as e:
            return False, f"could not read profile storage state: {e}"
    # 2. Clear the live browser context for that domain (persistent profile).
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=profile_dir(profile), headless=True
        )
        try:
            await context.clear_cookies()
            all_cookies = await context.cookies()
            keep = [c for c in all_cookies if not _domain_match(c.get("domain", ""), host)]
            removed += len(all_cookies) - len(keep)
            await context.add_cookies(keep)
        except Exception as e:  # noqa: BLE001 - warn, still done
            _warn(f"could not clear browser cookies for {host}: {e}")
        await context.close()
    return True, removed


def cmd_logout(site, profile):
    if not profile:
        print("error: --profile NAME is required for logout")
        return 2
    try:
        profile_dir(profile)
    except SystemExit as e:
        print(f"error: {e}")
        return 2
    ok, host_or_err = _valid_site_url(site)
    if not ok:
        print(f"error: invalid site URL: {host_or_err}")
        return 2
    ok, removed = asyncio.run(_logout_flow(site, profile))
    if not ok:
        print(f"error: {removed}")
        return 1
    print(f"Logged out {host_or_err} from profile '{profile}' ({removed} cookies cleared).")
    print("Other domains in this profile were left untouched.")
    return 0


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return

    (
        args,
        cookies,
        headers,
        max_chars_override,
        timeout_override,
        fresh,
        ttl,
        json_out,
        limit,
        strategy,
        profile,
        no_cache,
        headless,
    ) = parse_opts(args)

    if not args:
        print(__doc__)
        return

    if profile:
        try:
            os.makedirs(profile_dir(profile), exist_ok=True)
        except OSError as e:
            _warn(f"cannot create profile dir for '{profile}': {e}")

    cmd = args[0]
    if cmd == "search":
        cmd = "s"
    elif cmd == "fetch":
        cmd = "u"
    elif cmd == "search-fetch":
        cmd = "su"
    q = args[1] if len(args) > 1 else ""

    if cmd == "login":
        sys.exit(cmd_login(q, profile, headless))
    elif cmd == "profiles":
        cmd_profiles(json_out)
        return
    elif cmd == "logout":
        sys.exit(cmd_logout(q, profile))

    if cmd == "s":
        n = limit or (int(args[2]) if len(args) > 2 else 5)
        for i, r in enumerate(search(q, n=n)):
            print(f"{i + 1}. {r['title']}\n   {r['url']}\n   {r['snippet'][:200]}\n")

    elif cmd == "u":
        if q == "-":
            urls = [line.strip() for line in sys.stdin if line.strip()]
        else:
            urls = [q]
        max_chars = max_chars_override or 10000
        timeout = timeout_override or 20
        res = asyncio.run(
            scrape_many(
                urls,
                max_chars=max_chars,
                per_url_timeout=timeout,
                cookies=cookies,
                headers=headers,
                ttl=ttl,
                fresh=fresh,
                strategy=strategy,
                profile=profile,
                no_cache=no_cache,
            )
        )
        if json_out:
            print(json.dumps(res, indent=2))
            return
        for u_ in urls:
            r = res.get(u_, {})
            tag = r.get("method", "")
            stat = r.get("status", "")
            if stat != "success":
                auth = r.get("auth") or {}
                print(f"# {u_}  [{stat}/{tag}]")
                print(f"status={stat}")
                print(f"profile={auth.get('profile') or 'none'}")
                print(f"message={r.get('error') or 'unknown'}")
                continue
            print(f"# {r.get('title', '')}  [{stat}/{tag}]")
            if r.get("error"):
                print(f"ERROR: {r['error']}")
            print(f"{r.get('markdown', '')}\n")

    elif cmd == "su":
        n = limit or (int(args[2]) if len(args) > 2 else 3)
        max_chars = max_chars_override or 4000
        timeout = timeout_override or 20
        results = search(q, n=n)
        urls = [r["url"] for r in results]
        scraped = asyncio.run(
            scrape_many(
                urls,
                max_chars=max_chars,
                per_url_timeout=timeout,
                cookies=cookies,
                headers=headers,
                ttl=ttl,
                fresh=fresh,
                strategy=strategy,
                profile=profile,
                no_cache=no_cache,
            )
        )
        if json_out:
            out = {}
            for i, r in enumerate(results):
                got = scraped.get(r["url"], {})
                out[r["url"]] = {
                    "rank": i + 1,
                    "search_title": r["title"],
                    "snippet": r.get("snippet", ""),
                    "scrape_title": got.get("title", ""),
                    "markdown": got.get("markdown", ""),
                    "status": got.get("status", ""),
                    "method": got.get("method", ""),
                    "cached": got.get("cached", False),
                    "attempts": got.get("attempts", 0),
                    "error": got.get("error"),
                    "auth": got.get("auth")
                    or {"profile": None, "authenticated": None, "state": got.get("status", "")},
                }
            print(json.dumps(out, indent=2))
            return
        for i, r in enumerate(results):
            got = scraped.get(r["url"], {})
            stat = got.get("status", "")
            method = got.get("method", "")
            err = got.get("error") or ""
            fail = stat != "success"
            print(
                f"\n{'=' * 60}\n## {i + 1}. {r['title']}  [{stat}/{method}]{'  ' + err if err else ''}\n{'=' * 60}"
            )
            print(f"URL: {r['url']}")
            print(got.get("markdown", "")[:max_chars] if not fail else "(no content)")

    else:
        print("Unknown command:", cmd)


if __name__ == "__main__":
    main()
