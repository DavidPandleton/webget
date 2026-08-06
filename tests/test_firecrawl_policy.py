"""Phase 11b: strategy-ladder SSRF invariant + Firecrawl policy tests.

Invariant: if a URL is blocked by the SSRF policy, NO strategy fallback
may access it. Firecrawl runs remotely, so we cannot observe its request
stream; what webget CAN enforce is that a private URL is never SENT to
the provider (pre-check before the ladder), and that Firecrawl remains
strictly opt-in (key required, explicit strategy).
"""

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))


import webget_cli as webget


async def _many(urls, **kw):
    return await webget.scrape_many(urls, no_cache=True, **kw)


class TestLadderInvariant:
    def test_private_url_never_sent_to_firecrawl(self, server, monkeypatch):
        """Firecrawl is remote: the only guarantee we can make is that a
        blocked URL is never SENT to it. Spy on fetch_firecrawl."""
        monkeypatch.delenv("WEBGET_ALLOW_PRIVATE", raising=False)
        monkeypatch.setenv("WEBGET_FIRECRAWL_KEY", "sk-test")
        sent = []

        async def spy(url, **kw):
            sent.append(url)
            return {"status": "error", "error": "spy"}

        monkeypatch.setattr(webget, "fetch_firecrawl", spy)
        res = asyncio.run(_many([server.url("/private")], strategy="firecrawl"))
        assert sent == [], f"private URL was sent to Firecrawl: {sent}"
        assert res[server.url("/private")]["status"] == "error"

    def test_private_url_never_sent_to_firecrawl_via_auto(self, server, monkeypatch):
        """auto ladder with firecrawl key: private URL still blocked by
        pre-check before any strategy runs."""
        monkeypatch.delenv("WEBGET_ALLOW_PRIVATE", raising=False)
        monkeypatch.setenv("WEBGET_FIRECRAWL_KEY", "sk-test")
        sent = []

        async def spy(url, **kw):
            sent.append(url)
            return {"status": "error", "error": "spy"}

        monkeypatch.setattr(webget, "fetch_firecrawl", spy)
        res = asyncio.run(_many([server.url("/redirect-private")], strategy="auto"))
        assert sent == []
        assert res[server.url("/redirect-private")]["status"] == "error"

    def test_public_url_reaches_firecrawl_only_with_key(self, server, monkeypatch):
        """Firecrawl is opt-in: without a key, no provider call happens."""
        monkeypatch.delenv("WEBGET_FIRECRAWL_KEY", raising=False)
        monkeypatch.delenv("WEBGET_ALLOW_PRIVATE", raising=False)
        called = []

        async def spy(url, **kw):
            called.append(url)
            return {"status": "error", "error": "spy"}

        monkeypatch.setattr(webget, "fetch_firecrawl", spy)
        with pytest.raises(SystemExit):
            asyncio.run(_many([server.url("/normal")], strategy="firecrawl"))
        assert called == [], "firecrawl called without a key"

    def test_firecrawl_stays_opt_in_documented(self):
        """The strategy whitelist is the enforcement point for opt-in."""
        # ladder only reaches firecrawl when key is present (opt-in gate)
        assert webget.firecrawl_key() in ("", None)  # no key in test env
        assert webget.fetch_firecrawl.__doc__ is not None
        assert "key" in webget.fetch_firecrawl.__doc__.lower()


class TestFirecrawlRedirectLimitation:
    def test_documented_limitation_exists(self):
        """The remote redirect limitation must be documented in the fetch
        function docstring (no fake mitigation)."""
        doc = webget.fetch_firecrawl.__doc__ or ""
        assert any(w in doc.lower() for w in ("redirect", "third-party", "remote", "provider"))
