"""Firecrawl escape-hatch fetch for webget.

SECURITY LIMITATION (documented, not mitigated): the actual page fetch
is performed by Firecrawl on THEIR infrastructure. The Firecrawl API
provides no parameter to disable redirects, validate redirect
destinations, receive the redirect chain, or enforce allowed
hosts/IPs. webget guarantees only that a private/internal URL is
never SENT to Firecrawl (pre-check in scrape_many blocks it before the
ladder runs) and that Firecrawl is strictly opt-in.
"""

from __future__ import annotations

import os

import httpx


def firecrawl_key():
    return os.environ.get("WEBGET_FIRECRAWL_KEY", "").strip()


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
