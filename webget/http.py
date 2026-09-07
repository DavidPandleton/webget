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


_EMPTY_META = {"author": None, "published_at": None, "site_name": None, "language": None}


def _extract_with_metadata(html):
    """Extract (text, metadata) from HTML.

    Tries trafilatura JSON output (clean article text + author/date/site/
    language metadata) first, then markdownify as a text-only fallback
    (empty metadata). Returns a (str, dict) tuple; metadata keys are
    always author/published_at/site_name/language, values None when
    unknown.
    """
    try:
        import json

        import trafilatura

        raw = trafilatura.extract(
            html,
            output_format="json",
            with_metadata=True,
            include_comments=False,
            include_tables=True,
        )
        if raw:
            doc = json.loads(raw)
            text = (doc.get("text") or "").strip()
            if text and len(text) > 100:
                return text, {
                    "author": doc.get("author"),
                    "published_at": doc.get("date"),
                    "site_name": doc.get("sitename"),
                    "language": doc.get("language") or doc.get("lang"),
                }
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
        if len(converted) > 50:
            return converted, dict(_EMPTY_META)
        return "", dict(_EMPTY_META)
    except Exception:  # noqa: BLE001 - best-effort extraction, empty is fine
        return "", dict(_EMPTY_META)


def _extract_markdown(html):
    """Try trafilatura (clean article text) then markdownify (full markdown)."""
    text, _ = _extract_with_metadata(html)
    return text


def _convert_non_html(ctype, body, url):
    """Convert a non-HTML response body to (title, markdown, metadata).

    Routes by content-type: JSON -> pretty code block, text/* -> plain
    text, CSV -> GFM table, RSS/Atom/XML feeds -> link list, PDF ->
    per-page text via pypdf. Raises RuntimeError for unknown types.
    """
    from urllib.parse import urlparse

    host = urlparse(url).hostname or ""
    meta = {"author": None, "published_at": None, "site_name": host or None, "language": None}
    low = (ctype or "").lower()
    if "json" in low:
        try:
            import json as _json

            pretty = _json.dumps(
                _json.loads(body.decode("utf-8", errors="replace")),
                indent=2,
                ensure_ascii=False,
            )
        except Exception:  # noqa: BLE001 - malformed JSON falls back to raw text
            pretty = body.decode("utf-8", errors="replace")
        return url, f"```json\n{pretty}\n```", meta
    if low.startswith("text/"):
        if "csv" in low:
            return url, _csv_to_gfm(body), meta
        return url, body.decode("utf-8", errors="replace").strip(), meta
    if "xml" in low or "rss" in low or "atom" in low or "feed" in low:
        return url, _feed_to_links(body), meta
    if "pdf" in low:
        return url, _pdf_to_text(body), meta
    raise RuntimeError(f"not HTML ({ctype or 'unknown'})")


def _pdf_to_text(body):
    """Extract per-page text from PDF bytes via pypdf."""
    try:
        import io as _io

        from pypdf import PdfReader
    except ImportError:
        raise RuntimeError("PDF content requires 'pypdf' (pip install webget-cli)") from None
    reader = PdfReader(_io.BytesIO(body))
    parts = []
    for i, page in enumerate(reader.pages):
        try:
            t = page.extract_text() or ""
        except Exception:  # noqa: BLE001 - one bad page must not kill the doc
            t = ""
        if t.strip():
            parts.append(f"## Page {i + 1}\n\n{t.strip()}")
    text = "\n\n".join(parts)
    if not text.strip():
        raise RuntimeError("PDF has no extractable text")
    return text


def _feed_to_links(body):
    """Convert RSS/Atom XML bytes to a markdown link list."""
    import xml.etree.ElementTree as _ET

    try:
        root = _ET.fromstring(body)
    except Exception:  # noqa: BLE001 - malformed XML falls back to raw text
        return body.decode("utf-8", errors="replace").strip()
    lines = []
    for item in root.iter("item"):  # RSS
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = (item.findtext("description") or "").strip()
        if title and link:
            lines.append(f"- [{title}]({link})")
        elif title:
            lines.append(f"- {title}")
        if desc:
            lines.append(f"  > {desc[:200]}")
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for entry in root.findall(".//atom:entry", ns):  # Atom
        t = entry.find("atom:title", ns)
        title = (t.text or "").strip() if t is not None else ""
        link = ""
        for l in entry.findall("atom:link", ns):
            href = (l.get("href") or "").strip()
            if href and (l.get("rel", "alternate") == "alternate" or not l.get("rel")):
                link = href
                break
        s = entry.find("atom:summary", ns)
        desc = (s.text or "").strip() if s is not None else ""
        if title and link:
            lines.append(f"- [{title}]({link})")
        elif title:
            lines.append(f"- {title}")
        if desc:
            lines.append(f"  > {desc[:200]}")
    if lines:
        return "\n".join(lines)
    return body.decode("utf-8", errors="replace").strip()


def _csv_to_gfm(body):
    """Convert CSV bytes to a GitHub-flavored markdown table."""
    import csv as _csv
    import io as _io

    try:
        rows = list(_csv.reader(_io.StringIO(body.decode("utf-8", errors="replace"))))
    except Exception:  # noqa: BLE001 - malformed CSV falls back to raw text
        return body.decode("utf-8", errors="replace").strip()
    rows = [r for r in rows if r]
    if not rows:
        return ""
    esc = lambda c: (c or "").replace("|", "\\|")  # noqa: E731
    header = "| " + " | ".join(esc(c) for c in rows[0]) + " |"
    sep = "| " + " | ".join("---" for _ in rows[0]) + " |"
    lines = [header, sep]
    for r in rows[1:]:
        lines.append("| " + " | ".join(esc(c) for c in r) + " |")
    return "\n".join(lines)


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
            ip = await asyncio.wait_for(asyncio.to_thread(resolver, target), max(remaining, 0.1))
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
                raw_body = b"".join(chunks)
                low_ctype = ctype.lower()
                if "html" in low_ctype or (
                    "text" in low_ctype and "csv" not in low_ctype and "xml" not in low_ctype
                ):
                    html = raw_body.decode("utf-8", errors="replace")
                else:
                    title, md, meta = _convert_non_html(ctype, raw_body, current)
                    return {
                        "title": title,
                        "markdown": md[:max_chars],
                        "metadata": meta,
                        "non_html": True,
                        "status_code": r.status_code,
                        "html": "",
                    }
                title = ""
                m = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
                if m:
                    title = re.sub(r"\s+", " ", m.group(1)).strip()
                md, meta = await asyncio.to_thread(_extract_with_metadata, html)
                return {
                    "title": title,
                    "markdown": md[:max_chars],
                    "metadata": meta,
                    "status_code": r.status_code,
                    "html": html[:8000],
                }
