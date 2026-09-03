"""SSRF guard for webget.

Refuses to fetch loopback/private/link-local/unspecified addresses,
including hostnames that resolve to them and redirect hops. Protects
local services, cloud metadata (169.254.169.254), and other private hosts
from being reached through webget. Legitimate intranet use can opt out
with WEBGET_ALLOW_PRIVATE=1.

The browser-side guard (_guard_browser_routes) installs a Playwright
route handler that re-implements redirect following manually so every
hop is checked against the policy before being fetched.

The policy functions (`_is_private_target`, `_private_ip_for`) defer to
`webget_cli._is_private_target` / `_private_ip_for` at call time so that
`monkeypatch.setattr(webget, "_is_private_target", guarded)` reaches the
browser route guard (which lives in this module) as well as the ladder
pre-check in webget.ladder. In the original single-file webget the
definition and all call sites were in the same module; the package
layout would otherwise bind the symbol at import time and miss the
patch.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from urllib.parse import urljoin

# Per-process DNS cache. getaddrinfo has no timeout of its own; a sick
# resolver can block a thread (and, when called from the event loop, the
# whole loop) for minutes. Callers from async code must use
# _private_ip_for_async, which bounds this with asyncio.wait_for.
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


def _doh_resolve(host, timeout=3.0):
    """Fallback DoH resolver (Cloudflare 1.1.1.1 / Google 8.8.8.8) using httpx.
    Returns list of IP strings if resolvable, else empty list.
    """
    if os.environ.get("WEBGET_DISABLE_DOH") == "1":
        return []
    import urllib.parse

    import httpx

    urls = [
        f"https://1.1.1.1/dns-query?name={urllib.parse.quote(host)}&type=A",
        f"https://dns.google/resolve?name={urllib.parse.quote(host)}&type=A",
    ]
    for url in urls:
        try:
            with httpx.Client(timeout=timeout, verify=True) as client:
                res = client.get(url, headers={"accept": "application/dns-json"})
                if res.status_code == 200:
                    data = res.json()
                    answers = data.get("Answer", [])
                    ips = [
                        ans.get("data")
                        for ans in answers
                        if ans.get("type") == 1 and ans.get("data")
                    ]
                    if ips:
                        return ips
        except Exception:  # noqa: BLE001, S112 - DoH is best-effort fallback
            continue
    return []


def _resolve_hostname_ips(host):
    """Resolve a hostname to a list of IP address strings.
    Tries system getaddrinfo first; falls back to DoH on DNS resolution errors.
    """
    try:
        infos = socket.getaddrinfo(host, None)
        return [i[4][0] for i in infos]
    except OSError:
        doh_ips = _doh_resolve(host)
        return doh_ips


def _hostname_private(host):
    """Resolve a hostname once and check every address. Cached per process.

    getaddrinfo has no timeout of its own; a sick resolver can block a
    thread (and, when called from the event loop, the whole loop) for
    minutes. Callers from async code must use _private_ip_for_async,
    which bounds this with asyncio.wait_for.
    """
    if host in _PRIVATE_IP_CACHE:
        return _PRIVATE_IP_CACHE[host]
    ips = _resolve_hostname_ips(host)
    if not ips:
        # DNS failure is not a privacy violation; let the fetch fail normally.
        _PRIVATE_IP_CACHE[host] = False
        return False
    try:
        private = any(_ip_is_private(ipaddress.ip_address(ip)) for ip in ips)
    except ValueError:
        private = False
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
        target = bm.default_context if bm else None
        if target is None:
            return
    except Exception:  # noqa: BLE001 - guard is best-effort; pre-check still applies
        return

    async def guard(route, request):
        url = request.url
        # method/body handling: 301/302/303 upgrades redirects to GET per
        # HTTP spec; 307/308 preserve method+body.
        method = request.method
        # post_data decodes as UTF-8 and raises on binary/compressed
        # bodies; read the undecoded bytes instead (best-effort replay).
        body = _request_body_bytes(request)

        for _hop in range(21):
            # Consult the SSRF policy through the shim so test patches
            # (e.g. `webget._is_private_target = guarded`) reach this
            # browser-side guard. The shim's attribute is what tests
            # patch; the package module's is the default. Falling back
            # to the local _is_private_target keeps behavior intact when
            # no patch is active.
            try:
                import webget_cli as _shim

                _fn = getattr(_shim, "_is_private_target", None)
            except ImportError:
                _fn = None
            if _fn is not None and _fn is not _is_private_target:
                try:
                    is_private = _fn(url)
                except TypeError:
                    is_private = _fn(url)
            else:
                is_private = _is_private_target(url)
            if is_private:
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

    # crawler_strategy.browser_manager.default_context is a Playwright
    # Browser in some Crawl4AI versions and an already-created
    # BrowserContext in others (seen on the profile/session path, where
    # the manager builds its context up front). Handle both: a Browser
    # gets routes on every existing context plus a hook for lazily
    # created ones; a single BrowserContext is registered directly.
    if hasattr(target, "contexts"):
        for ctx in list(target.contexts):
            await register(ctx)
        # Crawl4AI may create the crawling context lazily on first navigation.
        target.on("context", lambda ctx: asyncio.create_task(register(ctx)))
    else:
        await register(target)
