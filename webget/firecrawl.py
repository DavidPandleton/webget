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

from .truncate import smart_truncate


def firecrawl_key():
    return os.environ.get("WEBGET_FIRECRAWL_KEY", "").strip()


def _as_mapping(value) -> dict:
    """Return `value` if it is a mapping, else an empty dict.

    Firecrawl's response shape is not contractual: `data` has been observed
    as a list and `metadata` as a non-mapping. Callers here only ever want
    keys, so anything that is not a mapping is treated as empty rather than
    handed to `.get` and raised on. `None` and mapping-like objects without
    `.get` both degrade to empty.
    """
    return value if isinstance(value, dict) else {}


def _coerce_status(value) -> int | None:
    """Coerce Firecrawl's metadata.statusCode into an int, or None.

    Firecrawl has returned this as an int and (in some responses) as a
    numeric string. Anything we cannot read as an HTTP status is treated
    as absent rather than guessed at, so a malformed field degrades to the
    old behaviour instead of reporting a bogus status.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        code = int(value)
    except (TypeError, ValueError, OverflowError):
        # OverflowError matters: json.loads accepts the non-standard
        # `Infinity`/`-Infinity` tokens, so a body carrying
        # {"statusCode": Infinity} reaches here as float('inf') and
        # int(inf) raises. `nan` is already covered by ValueError.
        return None
    return code if 100 <= code <= 599 else None


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
        data = _as_mapping(r.json().get("data"))
        md = data.get("markdown", "") or ""
        meta = _as_mapping(data.get("metadata"))
        # metadata.statusCode is the TARGET page's status, which is what
        # callers mean by status_code. r.status_code is only the transport
        # status to api.firecrawl.dev and is always 200 here (line 51 above
        # rejects anything else), so reporting it told callers nothing.
        target_status = _coerce_status(meta.get("statusCode"))
        if not md:
            # Name the target status when Firecrawl gives us one: a bare
            # "empty result" hides whether the page 403'd, 404'd, or was
            # genuinely blank, and that distinction decides what a caller
            # should do next.
            if target_status is not None:
                raise RuntimeError(
                    f"Firecrawl empty result (target returned HTTP {target_status})"
                )
            raise RuntimeError("Firecrawl empty result")
        return {
            "title": meta.get("title", ""),
            "markdown": smart_truncate(md, max_chars),
            # Laporkan status halaman target HANYA kalau Firecrawl
            # benar-benar memberikannya. Jangan jatuh ke r.status_code:
            # itu status transport ke api.firecrawl.dev, dan baris di atas
            # sudah menolak apa pun selain 200, jadi nilainya SELALU 200.
            # Melaporkannya membuat pemanggil melihat 200 untuk halaman
            # yang statusnya tidak diketahui - angka yang tampak seperti
            # status target (dan tampak seperti sukses) padahal hanya
            # menandakan bahwa permintaan ke Firecrawl sendiri berhasil.
            #
            # None adalah jawaban yang benar untuk "tidak tahu": pemanggil
            # bisa membedakannya dari 200. Komentar di atas sudah mengakui
            # fallback lama "told callers nothing"; sekarang tidak ada lagi.
            "status_code": target_status,
            "html": "",
        }
