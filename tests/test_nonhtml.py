"""Ide #2: non-HTML content routing (JSON/text/CSV/feed/PDF to markdown)."""

import asyncio

import webget_cli as webget


class TestJsonRouting:
    def test_fetch_json_returns_pretty_markdown(self, fresh_cache):
        url = fresh_cache.url("/json")
        res = asyncio.run(webget.fetch_http(url, 6000, timeout=10))
        assert "hello" in res["markdown"]
        assert "world" in res["markdown"]
        assert res["metadata"]["site_name"] == "127.0.0.1"

    def test_scrape_many_json_is_success(self, fresh_cache):
        url = fresh_cache.url("/json")
        out = asyncio.run(webget.scrape_many([url], strategy="http", no_cache=True))
        assert out[url]["status"] == "success"
        assert out[url]["method"] == "http"
        assert "hello" in out[url]["markdown"]


class TestCsvRouting:
    def test_csv_becomes_gfm_table(self):
        body = b"name,age\nbudi,25\nsiti,30\n"
        title, md, meta = webget._convert_non_html("text/csv", body, "https://example.com/d.csv")
        assert "| name | age |" in md
        assert "| budi | 25 |" in md
        assert meta["site_name"] == "example.com"

    def test_csv_escapes_pipes(self):
        body = b"a,b\nx|y,z\n"
        _, md, _ = webget._convert_non_html("text/csv", body, "https://example.com/d.csv")
        assert "x\\|y" in md


class TestFeedRouting:
    RSS = (
        b'<?xml version="1.0"?><rss version="2.0"><channel><title>Blog</title>'
        b"<item><title>Post A</title><link>https://ex.com/a</link>"
        b"<description>First post here</description></item>"
        b"<item><title>Post B</title><link>https://ex.com/b</link></item>"
        b"</channel></rss>"
    )
    ATOM = (
        b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">'
        b"<title>Blog</title>"
        b'<entry><title>Entry One</title><link href="https://ex.com/1"/>'
        b"<summary>Summary one</summary></entry>"
        b"</feed>"
    )

    def test_rss_becomes_link_list(self):
        title, md, meta = webget._convert_non_html("application/rss+xml", self.RSS, "https://ex.com/feed")
        assert "[Post A](https://ex.com/a)" in md
        assert "[Post B](https://ex.com/b)" in md
        assert "First post here" in md

    def test_atom_becomes_link_list(self):
        _, md, _ = webget._convert_non_html("application/atom+xml", self.ATOM, "https://ex.com/feed")
        assert "[Entry One](https://ex.com/1)" in md

    def test_xml_without_items_falls_back_to_text(self):
        _, md, _ = webget._convert_non_html(
            "application/xml", b"<note><to>u</to></note>", "https://ex.com/n.xml"
        )
        assert "to" in md


class TestPdfRouting:
    def test_pdf_routing_uses_pypdf(self, monkeypatch):
        import sys
        import types

        fake_page = types.SimpleNamespace(extract_text=lambda: "Hello PDF page one")
        fake_reader = lambda *a, **k: types.SimpleNamespace(pages=[fake_page])  # noqa: E731
        fake_mod = types.SimpleNamespace(PdfReader=fake_reader)
        monkeypatch.setitem(sys.modules, "pypdf", fake_mod)
        title, md, meta = webget._convert_non_html(
            "application/pdf", b"%PDF-fake", "https://ex.com/d.pdf"
        )
        assert "Hello PDF page one" in md
        assert meta["site_name"] == "ex.com"

    def test_pdf_missing_dep_errors_clearly(self, monkeypatch):
        import sys

        monkeypatch.setitem(sys.modules, "pypdf", None)
        import pytest

        with pytest.raises(RuntimeError, match="pypdf"):
            webget._convert_non_html("application/pdf", b"%PDF-fake", "https://ex.com/d.pdf")

    def test_pdf_empty_text_errors(self, monkeypatch):
        import sys
        import types

        fake_page = types.SimpleNamespace(extract_text=lambda: "  ")
        fake_reader = lambda *a, **k: types.SimpleNamespace(pages=[fake_page])  # noqa: E731
        fake_mod = types.SimpleNamespace(PdfReader=fake_reader)
        monkeypatch.setitem(sys.modules, "pypdf", fake_mod)
        import pytest

        with pytest.raises(RuntimeError, match="no extractable text"):
            webget._convert_non_html("application/pdf", b"%PDF-fake", "https://ex.com/d.pdf")
