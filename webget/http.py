"""HTTP fast-path fetch + markdown extraction for webget.

fetch_http() is the cheapest strategy: a plain HTTP GET, manual redirect
following so the SSRF guard runs on every hop, and a streaming body cap
so a giant/binary download cannot exhaust memory. Extraction tries
trafilatura first (clean article text), then markdownify as a fallback.
"""
from __future__ import annotations

import asyncio
import re
import time
import warnings
from urllib.parse import urlparse

import httpx

from .ssrf import SSRFError, _private_ip_for

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
    # One absolute wall-clock budget for the whole request: DNS guard,
    # connect, redirects, and body streaming all draw from it.
    deadline = time.monotonic() + timeout

    async def ssrf_guard(target):
        remaining = deadline - time.monotonic()
        try:
            # Resolve through the shim so test patches (e.g.
            # `webget._private_ip_for = guarded`) reach this fast path's
            # SSRF check; the package module's is the default.
            try:
                import webget_cli as _shim

                _fn = getattr(_shim, "_private_ip_for", None)
            except ImportError:
                _fn = None
            resolver = _fn if (_fn is not None and _fn is not _private_ip_for) else _private_ip_for
            ip = await asyncio.wait_for(
                asyncio.to_thread(resolver, target), max(remaining, 0.1)
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