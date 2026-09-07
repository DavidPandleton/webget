"""Ide #1: metadata from trafilatura exposed in fetch results."""

import webget_cli as webget


def _rich_html():
    return (
        "<html><head><title>T</title>"
        '<meta name="author" content="Jane Doe">'
        '<meta property="article:published_time" content="2026-09-01T10:00:00Z">'
        "</head><body><article><h1>T</h1><p>"
        + "Lorem ipsum dolor sit amet consectetur adipiscing elit sed do. " * 10
        + "</p></article></body></html>"
    )


class TestExtractWithMetadata:
    def test_returns_text_and_metadata(self):
        text, meta = webget._extract_with_metadata(_rich_html())
        assert len(text) > 100
        assert meta["author"] == "Jane Doe"
        assert "2026-09-01" in (meta.get("published_at") or "")

    def test_fallback_returns_empty_metadata(self, monkeypatch):
        import trafilatura

        monkeypatch.setattr(trafilatura, "extract", lambda *a, **k: None)
        body = "<p>" + "Fallback body content here yes indeed. " * 5 + "</p>"
        text, meta = webget._extract_with_metadata("<h1>H</h1>" + body)
        assert "# H" in text
        assert meta == {"author": None, "published_at": None, "site_name": None, "language": None}

    def test_extract_markdown_still_returns_str(self, monkeypatch):
        # Regression guard: old contract returns plain str, never tuple.
        import trafilatura

        monkeypatch.setattr(trafilatura, "extract", lambda *a, **k: None)
        body = "<p>" + "Fallback body content here yes indeed. " * 5 + "</p>"
        out = webget._extract_markdown("<h1>H</h1>" + body)
        assert isinstance(out, str)
        assert "# H" in out


class TestFetchHttpMetadata:
    def test_fetch_http_result_carries_metadata(self, fresh_cache):
        import asyncio

        res = asyncio.run(webget.fetch_http(fresh_cache.url("/long"), 6000, timeout=10))
        assert set(res["metadata"]) == {"author", "published_at", "site_name", "language"}

    def test_scrape_many_success_carries_metadata(self, fresh_cache):
        import asyncio

        out = asyncio.run(webget.scrape_many([fresh_cache.url("/long")], strategy="http"))
        url = fresh_cache.url("/long")
        assert out[url]["status"] == "success"
        assert set(out[url]["metadata"]) == {
            "author",
            "published_at",
            "site_name",
            "language",
        }

    def test_cached_hit_carries_metadata(self, fresh_cache):
        import asyncio

        url = fresh_cache.url("/long")
        asyncio.run(webget.scrape_many([url], strategy="http"))
        out = asyncio.run(webget.scrape_many([url], strategy="http"))
        assert out[url]["cached"] is True
        assert set(out[url]["metadata"]) == {
            "author",
            "published_at",
            "site_name",
            "language",
        }
