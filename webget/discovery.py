import re
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse

import httpx

from .ssrf import _is_private_target

# Batas hop redirect manual. Sepadan dengan batas 20 di fetch_http
# (http.py); discovery cukup 10 karena sitemap jarang berpindah berkali-kali.
_MAX_REDIRECT_HOP = 10


def _extract_sitemap_urls(xml_content):
    urls = []
    try:
        root = ET.fromstring(xml_content)
        # Handle namespaces like {http://www.sitemaps.org/schemas/sitemap/0.9}loc
        for elem in root.iter():
            if elem.tag.endswith("loc") and elem.text:
                text = elem.text.strip()
                if text.startswith(("http://", "https://")):
                    urls.append(text)
    except Exception:  # noqa: BLE001
        # Simple regex fallback if malformed XML
        matches = re.findall(r"<loc>\s*(https?://[^\s<]+)\s*</loc>", xml_content, re.IGNORECASE)
        urls.extend(matches)
    return urls


async def discover_urls(
    target_url,
    limit=100,
    timeout=10,
    headers=None,
    allow_private=None,
):
    """Discover URLs for a domain by checking standard sitemap endpoints and robots.txt.
    Returns list of URLs bounded by limit.
    """
    if _is_private_target(target_url, allow_private=allow_private):
        return []

    parsed = urlparse(target_url)
    if not parsed.scheme or not parsed.netloc:
        return []

    base_origin = f"{parsed.scheme}://{parsed.netloc}"
    sitemap_candidates = [
        urljoin(base_origin, "/sitemap.xml"),
        urljoin(base_origin, "/sitemap_index.xml"),
        urljoin(base_origin, "/sitemap/sitemap.xml"),
    ]

    discovered = set()
    req_headers = {"User-Agent": "webget/discovery"}
    if headers:
        req_headers.update(headers)

    # follow_redirects=False SENGAJA: dengan True, httpx mengikuti 302
    # di dalam client.get() dan guard tidak pernah menilai hop itu. Domain
    # publik yang membalas redirect ke 127.0.0.1 atau 169.254.169.254
    # (cloud metadata) akan diikuti. fetch_http di http.py memakai pola
    # yang sama: redirect manual + guard tiap hop.
    async def _ambil_aman(client, url, headers):
        """GET dengan pengecekan SSRF di setiap hop redirect."""
        saat_ini = url
        for _ in range(_MAX_REDIRECT_HOP):
            if _is_private_target(saat_ini, allow_private=allow_private):
                return None
            resp = await client.get(saat_ini, headers=headers)
            if resp.status_code in (301, 302, 303, 307, 308):
                tujuan = resp.headers.get("location")
                if not tujuan:
                    return resp
                saat_ini = str(httpx.URL(saat_ini).join(tujuan))
                continue
            return resp
        return None

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        # First check robots.txt for custom Sitemap directives
        try:
            robots_resp = await _ambil_aman(client, urljoin(base_origin, "/robots.txt"),
                                           req_headers)
            if robots_resp is not None and robots_resp.status_code == 200:
                for line in robots_resp.text.splitlines():
                    if line.strip().lower().startswith("sitemap:"):
                        sm = line.split(":", 1)[1].strip()
                        if sm.startswith(("http://", "https://")):
                            sitemap_candidates.insert(0, sm)
        except Exception:  # noqa: BLE001, S110
            pass

        # Check sitemaps
        for sm_url in sitemap_candidates:
            if len(discovered) >= limit:
                break
            if _is_private_target(sm_url, allow_private=allow_private):
                continue
            try:
                resp = await _ambil_aman(client, sm_url, req_headers)
                if resp is not None and resp.status_code == 200 and resp.text:
                    found = _extract_sitemap_urls(resp.text)
                    for u in found:
                        # Check sub-sitemaps if any
                        if (
                            (u.endswith(".xml") or "sitemap" in u)
                            and u not in sitemap_candidates
                            and len(sitemap_candidates) < 10
                        ):
                            sitemap_candidates.append(u)
                        else:
                            discovered.add(u)
                            if len(discovered) >= limit:
                                break
            except Exception:  # noqa: BLE001, S112
                continue

    return sorted(discovered)[:limit]
