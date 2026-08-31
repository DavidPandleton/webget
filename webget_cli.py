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
  --concurrency N     Max concurrent fetches for batch runs (default: 10)
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
import ipaddress
import json
import os
import re
import socket
import sys
import tempfile
import time
import warnings


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
# Entries older than this can never be served by any reasonable ttl and are
# swept on write. 30 days matches the default CLI ttl.
_CACHE_SWEEP_TTL = 3600 * 24 * 30
# How long a per-domain strategy memory entry stays trusted. Sites change
# (a JS-heavy page may start server-rendering); a stale preference would
# skip the cheap HTTP path forever, so entries expire.
_STRATEGY_MEMORY_TTL = 3600 * 24 * 14


def _cache_path(url, cookies, headers, max_chars, profile=None):
    # Cookies and headers are sorted so semantically identical states (same
    # cookie set in different order) share one cache entry.
    ck = sorted(cookies or [], key=lambda c: (c.get("domain", ""), c.get("name", "")))
    hd = {k: v for k, v in sorted((headers or {}).items())}
    key = hashlib.sha1(
        f"{profile or 'public'}|{url}|{max_chars}|"
        f"{json.dumps(ck, sort_keys=True)}|"
        f"{json.dumps(hd, sort_keys=True)}".encode()
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
    tmp = None
    try:
        # Atomic write: each writer gets its OWN unique tmp file (mkstemp),
        # then an atomic rename. A shared "<path>.tmp" would let concurrent
        # writers interleave bytes in the same file, so the rename would
        # publish a corrupt document. mkstemp guarantees uniqueness per
        # writer and lives in the same directory (same filesystem, so
        # os.replace stays atomic).
        fd, tmp = tempfile.mkstemp(
            dir=os.path.dirname(p), prefix=os.path.basename(p) + ".", suffix=".tmp"
        )
        with os.fdopen(fd, "w") as f:
            json.dump({**data, "fetched_at": time.time()}, f)
        os.replace(tmp, p)
        tmp = None  # consumed by the rename
        # Eviction (lazy: only scans when actually over the cap, so the hot
        # write path stays cheap). Drop expired entries first - they can
        # never be served - then keep newest 80% if still over. The cap is
        # tunable via WEBGET_CACHE_MAX because a 500-file ceiling makes
        # bulk crawls (thousands of URLs) thrash: every write evicts
        # useful entries.
        try:
            cap = int(os.environ.get("WEBGET_CACHE_MAX", "5000"))
        except ValueError:
            cap = 5000
        try:
            files = [
                os.path.join(CACHE_DIR, f)
                for f in os.listdir(CACHE_DIR)
                if f.endswith(".json") and f != "strategy_memory.json"
            ]
            if len(files) > cap:
                now = time.time()
                live = []
                for fp in files:
                    try:
                        age = now - os.path.getmtime(fp)
                    except OSError:
                        continue
                    if age > _CACHE_SWEEP_TTL:
                        try:
                            os.remove(fp)
                        except OSError:
                            pass
                    else:
                        live.append(fp)
                if len(live) > cap:
                    live.sort(key=os.path.getmtime)
                    for f in live[: len(live) - int(cap * 0.8)]:
                        try:
                            os.remove(f)
                        except OSError:
                            pass
        except OSError:
            pass
    except OSError:
        pass  # cache is best-effort
    finally:
        if tmp is not None:
            try:
                os.remove(tmp)
            except OSError:
                pass


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
    concurrency = None
    remaining = []
    i = 0
    while i < len(args):
        if args[i] in ("-c", "--cookies") and i + 1 < len(args):
            cookies = parse_cookie_file(args[i + 1])
            i += 2
        elif args[i] == "--profile" and i + 1 < len(args):
            profile = args[i + 1]
            i += 2
        elif args[i] == "--concurrency" and i + 1 < len(args):
            concurrency = int(args[i + 1])
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
        concurrency,
    )


def _extract_markdown(html):
    """Try trafilatura (clean article text) then markdownify (full markdown)."""
    try:
        import trafilatura

        text = trafilatura.extract(html, include_comments=False, include_tables=True)
        if text and len(text.strip()) > 100:
            return text.strip()
    except Exception:  # noqa: BLE001, S110 - extraction libs vary; fall through
        pass
    try:
        from markdownify import markdownify as md

        # Feeds/sitemaps served as text/html make BeautifulSoup (via
        # markdownify) warn per document; the HTML parser still produces
        # usable markdown, so the warning is noise for a CLI.
        with warnings.catch_warnings():
            try:
                from bs4 import XMLParsedAsHTMLWarning

                warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
            except ImportError:
                pass
            # bullets="*" and heading_style="ATX" match the previous html2text
            # output style (verified differential 2026-08-08) so the fallback
            # stays close to 0.7.2 (semantic parity).
            converted = md(html, bullets="*", heading_style="ATX").strip()
        return converted if len(converted) > 50 else ""
    except Exception:  # noqa: BLE001 - best-effort extraction, empty is fine
        return ""


# Max response body webget will read from the HTTP fast path (bytes).
# Guards against memory exhaustion from giant/binary downloads.
MAX_RESPONSE_BYTES = 25 * 1024 * 1024


class ResponseTooLarge(Exception):
    """HTTP response exceeded MAX_RESPONSE_BYTES.

    Raised by the streaming cap in fetch_http. This is a TERMINAL state
    for the ladder: retrying a 30MB page in a browser (crawl4ai) would
    just re-download the same giant body through Chromium, so escalating
    is pure waste. scrape_many treats it as terminal, not a ladder step.
    """


# Default cap on concurrent fetches per ladder pass. Unbounded gather on a
# 500-URL batch would open 500 connections (and later 500 browser pages).
_DEFAULT_CONCURRENCY = 10


async def fetch_http(url, max_chars, cookies=None, headers=None, timeout=15):
    """Fast path: plain HTTP GET + local markdown extraction.

    SSRF guard: the initial URL is checked; redirect hops are followed
    MANUALLY (follow_redirects=False) so every hop is checked against the
    private-address policy before being requested. Response body is read
    with a hard cap (MAX_RESPONSE_BYTES).

    The guard runs the (blocking) resolver in a worker thread bounded by
    the request timeout: a sick DNS server must not stall the event loop
    and stretch every httpx timer in a concurrent batch.
    """
    import httpx

    # One absolute wall-clock budget for the whole request: DNS guard,
    # connect, redirects, and body streaming all draw from it.
    deadline = time.monotonic() + timeout

    async def ssrf_guard(target):
        remaining = deadline - time.monotonic()
        try:
            ip = await asyncio.wait_for(
                asyncio.to_thread(_private_ip_for, target), max(remaining, 0.1)
            )
        except TimeoutError:
            raise TimeoutError(f"DNS resolution exceeded {timeout}s") from None
        if ip is not None:
            raise SSRFError(f"blocked by SSRF guard: {target} resolves to private address {ip}")

    await ssrf_guard(url)

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
        now = time.time()
        if host:
            for c in cookies:
                d = (c.get("domain") or "").lstrip(".").lower()
                if d and (host == d or host.endswith("." + d)):
                    exp = c.get("expires") or -1
                    # Skip expired cookies: session cookies (expires<0) and
                    # future-expiry cookies are sent; past-expiry are not.
                    if 0 <= exp < now:
                        continue
                    cj[c["name"]] = c["value"]

    current = url
    redirects = 0
    async with httpx.AsyncClient(
        follow_redirects=False, timeout=timeout, headers=hdrs, cookies=cj
    ) as client:
        while True:
            await ssrf_guard(current)
            # stream=True is REQUIRED: client.get() would buffer the whole
            # body into memory before our cap could stop it.
            async with client.stream("GET", current) as r:
                if r.status_code in (301, 302, 303, 307, 308):
                    loc = r.headers.get("location")
                    if not loc:
                        break
                    redirects += 1
                    if redirects > 20:
                        raise RuntimeError("too many redirects")
                    current = str(httpx.URL(current).join(loc))
                    continue
                ctype = r.headers.get("content-type", "")
                if "html" not in ctype and "text" not in ctype:
                    raise RuntimeError(f"not HTML ({ctype or 'unknown'})")
                # Read with a hard cap while streaming, so a giant/binary
                # body cannot exhaust memory. httpx's timeout bounds a
                # single socket operation only, so a server that slow-drips
                # the body in small chunks over minutes can keep it alive
                # far past the deadline; enforce an absolute wall-clock cap
                # here so one slow URL cannot stall the whole batch.
                chunks = []
                total = 0
                async for chunk in r.aiter_bytes():
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"streaming body exceeded {timeout}s deadline")
                    total += len(chunk)
                    if total > MAX_RESPONSE_BYTES:
                        raise ResponseTooLarge(f"response too large (> {MAX_RESPONSE_BYTES} bytes)")
                    chunks.append(chunk)
                html = b"".join(chunks).decode("utf-8", errors="replace")
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


# ---------- SSRF guard ----------
#
# Default policy: refuse to fetch loopback/private/link-local/unspecified
# addresses, INCLUDING hostnames that resolve to them and redirect hops.
# This protects local services, cloud metadata (169.254.169.254), and other
# private hosts from being reached through webget. Legitimate intranet use
# can opt out with WEBGET_ALLOW_PRIVATE=1 (set by the caller deliberately).

_PRIVATE_IP_CACHE = {}


def _ip_is_private(ip):
    return (
        False
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _hostname_private(host):
    """Resolve a hostname once and check every address. Cached per process.

    getaddrinfo has no timeout of its own; a sick resolver can block a
    thread (and, when called from the event loop, the whole loop) for
    minutes. Callers from async code must use _private_ip_for_async,
    which bounds this with asyncio.wait_for.
    """
    if host in _PRIVATE_IP_CACHE:
        return _PRIVATE_IP_CACHE[host]
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        # DNS failure is not a privacy violation; let the fetch fail normally.
        _PRIVATE_IP_CACHE[host] = False
        return False
    private = any(_ip_is_private(ipaddress.ip_address(i[4][0])) for i in infos)
    if len(_PRIVATE_IP_CACHE) < 512:
        _PRIVATE_IP_CACHE[host] = private
    return private


def _private_ip_for(url):
    """Return the offending private IP literal for url, or None if safe.

    Same policy as _is_private_target but reports WHICH address tripped
    the guard, so the error message names an IP instead of looking like
    a false positive on a public domain.
    """
    if os.environ.get("WEBGET_ALLOW_PRIVATE") == "1":
        return None
    from urllib.parse import urlparse

    host = urlparse(url).hostname
    if not host:
        return None  # malformed URL; other validation handles it
    host = host.strip("[]").lower().rstrip(".")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host if _hostname_private(host) else None
    return host if _ip_is_private(ip) else None


def _is_private_target(url, allow_private=None):
    """True if url targets a private/loopback/link-local address, either by
    literal IP or by hostname resolution."""
    if allow_private is None:
        allow_private = os.environ.get("WEBGET_ALLOW_PRIVATE") == "1"
    if allow_private:
        return False
    return _private_ip_for(url) is not None


class SSRFError(RuntimeError):
    """Raised when a fetch would target a private address."""


def _request_body_bytes(request):
    """Raw request body as bytes for redirect replay, or None.

    Playwright's `request.post_data` decodes the body as UTF-8 text and
    raises UnicodeDecodeError on binary/compressed bodies (e.g. gzip
    POSTs, seen on facebook/linkedin). The browser SSRF route guard
    replays the raw body on manual redirect hops, so it must read the
    undecoded bytes. Body handling is BEST-EFFORT and must never be part
    of the SSRF decision, which is URL/IP policy only.
    """
    try:
        return request.post_data_buffer
    except Exception:  # noqa: BLE001 - body replay is best-effort
        return None


async def _guard_browser_routes(crawler_ctx):
    """Install a Playwright route guard so the browser can never request a
    private address, even through redirects or subresources.

    Crawl4AI does not expose a per-hop URL hook, and Playwright route
    handlers only fire for the FIRST request of a redirect chain (verified
    experimentally: the second request of a 302 chain never re-enters the
    route handler, it is followed inside Chromium). So EVERY request -
    navigation AND subresource - is fetched manually hop-by-hop with
    max_redirects=0, the SSRF policy is checked at every hop, and only a
    fully-vetted response is fulfilled to the browser. A redirect inside a
    subresource therefore cannot escape the policy: the hop target is
    checked BEFORE it is fetched.
    """
    try:
        bm = crawler_ctx.crawler_strategy.browser_manager
        browser = bm.default_context if bm else None
        if browser is None or not hasattr(browser, "on"):
            return
    except Exception:  # noqa: BLE001 - guard is best-effort; pre-check still applies
        return

    async def guard(route, request):
        from urllib.parse import urljoin

        url = request.url
        # method/body handling: 301/302/303 upgrades redirects to GET per
        # HTTP spec; 307/308 preserve method+body.
        method = request.method
        # post_data decodes as UTF-8 and raises on binary/compressed
        # bodies; read the undecoded bytes instead (best-effort replay).
        body = _request_body_bytes(request)

        for _hop in range(21):
            if _is_private_target(url):
                try:
                    await route.abort()
                except Exception:  # noqa: BLE001, S110 - already aborted
                    pass
                return
            try:
                resp = await route.fetch(
                    url=url,
                    method=method,
                    headers=request.headers,
                    post_data=body,
                    max_redirects=0,
                )
            except Exception:  # noqa: BLE001 - fetch unsupported (e.g. ws),
                # let the browser handle it; the initial URL was already
                # checked and most requests succeed through the manual path.
                try:
                    await route.continue_()
                except Exception:  # noqa: BLE001, S110
                    pass
                return
            if resp.status in (301, 302, 303, 307, 308):
                loc = resp.headers.get("location")
                if not loc:
                    break
                url = urljoin(url, loc)
                if resp.status in (301, 302, 303):
                    method = "GET"
                    body = None
                continue
            try:
                await route.fulfill(response=resp)
            except Exception:  # noqa: BLE001, S110 - page closed
                pass
            return
        try:
            await route.abort()
        except Exception:  # noqa: BLE001, S110
            pass

    async def register(ctx):
        try:
            await ctx.route("**/*", guard)
        except Exception:  # noqa: BLE001, S110 - context closing; skip
            pass

    for ctx in list(browser.contexts):
        await register(ctx)
    # Crawl4AI may create the crawling context lazily on first navigation.
    browser.on("context", lambda ctx: asyncio.create_task(register(ctx)))


def _profile_root():
    """Profile root dir; WEBGET_PROFILE_DIR env override for tests/ops.

    Order: env override first, then a monkeypatched/overridden PROFILE_DIR
    module attribute (existing tests patch it directly), then the default.
    """
    env = os.environ.get("WEBGET_PROFILE_DIR")
    if env:
        return env
    patched = globals().get("PROFILE_DIR")
    if patched:
        return patched
    return os.path.expanduser("~/.local/share/webget/profiles")


PROFILE_DIR = _profile_root()
_PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


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


async def fetch_firecrawl(url, max_chars, key, timeout=30):
    """Firecrawl escape hatch: POST /v1/scrape, formats markdown.

    SECURITY LIMITATION (documented, not mitigated): the actual page fetch
    is performed by Firecrawl on THEIR infrastructure. The Firecrawl API
    provides no parameter to disable redirects, validate redirect
    destinations, receive the redirect chain, or enforce allowed
    hosts/IPs (verified against docs.firecrawl.dev 2026-08). webget
    therefore guarantees only:
      - a private/internal URL is never SENT to Firecrawl (pre-check in
        scrape_many blocks it before the ladder runs);
      - Firecrawl is strictly opt-in (requires WEBGET_FIRECRAWL_KEY and an
        explicit strategy="firecrawl" or auto ladder with key).
    What webget CANNOT control: any redirect Firecrawl follows after the
    initial URL, including redirects into addresses that resolve only on
    Firecrawl's network. This is a REMOTE-provider limitation, distinct
    from local SSRF protection (HTTP/browser paths fetch from this
    machine and are fully guarded). Do not rely on Firecrawl as an SSRF
    boundary; treat URLs sent to it as visible to a third party.
    """
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


def _strategy_memory_path():
    return os.path.join(CACHE_DIR, "strategy_memory.json")


def _load_strategy_memory():
    p = _strategy_memory_path()
    try:
        with open(p) as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    # Normalize to {domain: {"method": str, "ts": float}}. Entries older
    # than the expiry window are dropped: a site that once needed a
    # browser may serve plain HTML today (server-side rendering changes),
    # and a stale "crawl4ai" preference would skip the cheap HTTP path
    # forever. Plain-string values are the pre-expiry format; treat them
    # as fresh so an upgrade does not wipe a working memory.
    out = {}
    now = time.time()
    for domain, val in raw.items():
        if isinstance(val, str):
            out[domain] = {"method": val, "ts": now}
        elif isinstance(val, dict) and isinstance(val.get("method"), str):
            ts = val.get("ts", 0)
            if now - ts <= _STRATEGY_MEMORY_TTL:
                out[domain] = {"method": val["method"], "ts": ts}
    return out


def _save_strategy_memory(memory):
    p = _strategy_memory_path()
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), prefix="strategy_memory.", suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(memory, f)
        os.replace(tmp, p)
    except OSError:
        pass  # best-effort


def _learn_strategy(domain, method):
    """Record that `method` succeeded for `domain`.

    Called after each successful fetch so future 'auto' ladder runs
    can try the known-good strategy first, skipping steps that are
    likely to fail (e.g. HTTP for a JS-heavy single-page app).
    Only writes when the value actually changes.
    """
    memory = _load_strategy_memory()
    if (memory.get(domain) or {}).get("method") == method:
        return
    memory[domain] = {"method": method, "ts": time.time()}
    _save_strategy_memory(memory)


def _reorder_steps_by_domain(steps, url):
    """Reorder ladder steps so the domain's preferred strategy comes first.

    When strategy='auto' and we have a memory of past success for this
    domain, promote that method to the front so the ladder skips likely
    failures. Unchanged when memory is empty or strategy is explicit.
    """
    if len(steps) <= 1:
        return steps
    from urllib.parse import urlparse

    domain = urlparse(url).hostname or ""
    if not domain:
        return steps
    memory = _load_strategy_memory()
    entry = memory.get(domain)
    preferred = entry.get("method") if entry else None
    if not preferred or preferred not in steps:
        return steps
    return [preferred] + [s for s in steps if s != preferred]


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
        "reasons": [],
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
    max_concurrency=None,
):
    steps = _ladder(strategy, firecrawl_key())
    # Per-domain strategy memory: when strategy is "auto" and we have
    # past success data for the batch's majority domain, reorder steps
    # so the known-good strategy is tried first, skipping likely failures.
    if strategy == "auto" and urls:
        from collections import Counter
        from urllib.parse import urlparse

        domains = [urlparse(u).hostname for u in urls if urlparse(u).hostname]
        if domains:
            majority = Counter(domains).most_common(1)[0][0]
            steps = _reorder_steps_by_domain(steps, f"https://{majority}/")
    results = {}
    missing = []
    # Deduplicate: identical URLs must not cause duplicate work or races.
    seen = set()
    candidates = []
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        candidates.append(u)
    # SSRF guard applies to every strategy, not just the HTTP fast path:
    # never hand a private target to the browser or to Firecrawl either.
    # The check resolves hostnames (blocking); run them concurrently in
    # worker threads with a hard cap so one sick DNS server cannot stall
    # the event loop and stretch every timer in the batch. The semaphore
    # bounds in-flight lookups: the default executor has ~32 threads, and
    # an unbounded gather would queue later URLs past their own deadline.
    dns_sem = asyncio.Semaphore(32)

    async def _ssrf_verdict(u):
        async with dns_sem:
            try:
                blocked = await asyncio.wait_for(
                    asyncio.to_thread(_is_private_target, u), per_url_timeout
                )
            except TimeoutError:
                return u, f"DNS resolution exceeded {per_url_timeout}s"
            if not blocked:
                return u, None
            # Name the offending address when we can (best-effort: callers
            # that patch _is_private_target may not have a matching
            # _private_ip_for).
            ip = await asyncio.to_thread(_private_ip_for, u)
            return u, f"resolves to private address {ip}" if ip else "targets a private address"

    verdicts = dict(await asyncio.gather(*(_ssrf_verdict(u) for u in candidates)))
    for u in candidates:
        bad = verdicts.get(u)
        if bad:
            results[u] = {
                "title": "",
                "markdown": "",
                "status": "error",
                "method": "",
                "cached": False,
                "attempts": 0,
                "error": f"blocked by SSRF guard: {u} {bad}",
                "auth": {"profile": profile, "authenticated": None, "state": "error"},
                "reasons": [{"state": "error", "method": "", "detail": bad}],
            }
            continue
        hit = None if no_cache else cache_get(u, cookies, headers, max_chars, ttl, fresh, profile)
        if hit:
            results[u] = _normalize_hit(hit)
        else:
            missing.append(u)
    if not missing:
        return results

    sem = asyncio.Semaphore(max_concurrency or _DEFAULT_CONCURRENCY)

    attempts = {u: 0 for u in missing}
    reasons = {u: [] for u in missing}
    pending = list(missing)

    async def record(url, method, res=None, exc=None):
        """Classify one strategy result; return success dict or None (keep climbing)."""
        attempts[url] += 1
        if exc is not None or res is None:
            # Some httpx exceptions (ReadTimeout, ConnectTimeout) stringify
            # to the empty string; a bare "" error is useless to callers,
            # so fall back to the exception type name.
            detail = str(exc) if exc is not None else "no result"
            if not detail.strip():
                detail = type(exc).__name__
            reasons[url].append(("error", method, detail))
            return None
        state, authenticated = _auth_state(res, profile)
        if state == "success" and len((res.get("markdown") or "").strip()) >= 100:
            auth = {"profile": profile, "authenticated": authenticated, "state": state}
            # Record which strategy won for this domain so future 'auto'
            # batches can try it first (per-domain strategy memory).
            from urllib.parse import urlparse

            domain = urlparse(url).hostname
            if domain:
                _learn_strategy(domain, method)
            out = {
                "title": res.get("title", ""),
                "markdown": res.get("markdown", ""),
                "status": "success",
                "method": method,
                "cached": False,
                "attempts": attempts[url],
                "error": None,
                "auth": auth,
                # Consistent shape: failures carry the ladder chain; successes
                # carry an empty list (never null) so consumers can always
                # iterate reasons without a None check.
                "reasons": [],
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
            async with sem:
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
                except ResponseTooLarge as e:
                    # TERMINAL: the body exceeded MAX_RESPONSE_BYTES. A browser
                    # pass would re-download the same giant body through
                    # Chromium, so escalating is pure waste. Report it as a
                    # final error and drop the URL from the ladder.
                    return url, {
                        "title": "",
                        "markdown": "",
                        "status": "error",
                        "method": "http",
                        "cached": False,
                        "attempts": 1,
                        "error": str(e),
                        "auth": {
                            "profile": profile,
                            "authenticated": None,
                            "state": "error",
                        },
                        "reasons": [{"state": "error", "method": "http", "detail": str(e)}],
                    }
                except Exception as e:  # noqa: BLE001 - record reason, ladder continues
                    return url, await record(url, "http", exc=e)

        for url, out in await asyncio.gather(*(http_one(u) for u in pending)):
            if out:
                results[url] = out
        pending = [u for u in pending if u not in results]

    # Pass 2: Crawl4AI browser - only launched if something still needs it.
    if pending and "crawl4ai" in steps:
        try:
            from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
        except ImportError:
            _warn(
                "crawl4ai is not installed; install it with "
                "'pip install webget-cli[browser]' or 'uv tool install "
                "webget-cli --with webget-cli[browser]'"
            )
            for url in pending:
                reasons[url].append(("error", "crawl4ai", "crawl4ai not installed"))
            # Don't clear pending: keep URLs so the ladder can continue
            # (e.g. firecrawl fallback) and so terminal state is written below.
        else:
            if pending:
                bc = BrowserConfig(
                    use_persistent_context=bool(profile),
                    user_data_dir=profile_dir(profile) if profile else None,
                    cookies=cookies or None,
                    headers=headers or None,
                )
                cfg = CrawlerRunConfig()
                async with AsyncWebCrawler(config=bc, verbose=False) as crawler_ctx:
                    await _guard_browser_routes(crawler_ctx)

                    async def crawl_one(url):
                        async with sem:
                            try:
                                res = await _crawl4ai_once(crawler_ctx, cfg, url, per_url_timeout)
                                res["markdown"] = res.get("markdown", "")[:max_chars]
                                return url, await record(url, "crawl4ai", res=res)
                            except TimeoutError:
                                return url, await record(
                                    url, "crawl4ai", exc=TimeoutError("timeout")
                                )
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
                                await bm.default_context.storage_state(
                                    path=profile_state_path(profile)
                                )
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
            async with sem:
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
        state, authenticated, detail, method = _terminal_state(reasons[url], profile)
        auth = {"profile": profile, "authenticated": authenticated, "state": state}
        results[url] = {
            "title": "",
            "markdown": "",
            "status": state,
            "method": method,
            "cached": False,
            "attempts": attempts[url],
            "error": detail,
            "auth": auth,
            "reasons": [{"state": s, "method": m, "detail": d} for s, m, d in reasons[url]],
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
                return state, authenticated, detail, method
    first = reasons[0] if reasons else None
    method = first[1] if first else ""
    return "error", None, "; ".join(d for _, _, d in reasons), method


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
    """Sync helper for to_thread: atomic JSON write (unique tmp + rename)."""
    fd, tmp = tempfile.mkstemp(
        dir=os.path.dirname(path), prefix=os.path.basename(path) + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


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


def cmd_profiles(json_out):
    if not os.path.isdir(_profile_root()):
        if json_out:
            print("{}")
        else:
            print("PROFILE     LAST USED     SIZE       STATUS\n(no profiles yet)")
        return
    profiles = list_profiles()
    if json_out:
        print(json.dumps({p["profile"]: p for p in profiles}, indent=2))
        return
    print(f"{'PROFILE':<12} {'LAST USED':<12} {'SIZE':>10}  STATUS")
    for p in profiles:
        print(
            f"{p['profile']:<12} {_fmt_age(p['last_used']):<12} "
            f"{p['size'] / 1024 / 1024:>8.1f} MB  {p['status']}"
        )


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
        concurrency,
    ) = parse_opts(args)

    if concurrency is not None and concurrency < 1:
        print("error: --concurrency must be >= 1")
        sys.exit(2)

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
        try:
            results = search(q, n=n)
        except Exception as e:  # noqa: BLE001 - surface a clean error, not a traceback
            print(f"error: search failed: {e}")
            sys.exit(1)
        for i, r in enumerate(results):
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
                max_concurrency=concurrency,
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
        try:
            results = search(q, n=n)
        except Exception as e:  # noqa: BLE001 - surface a clean error, not a traceback
            print(f"error: search failed: {e}")
            sys.exit(1)
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
                max_concurrency=concurrency,
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
