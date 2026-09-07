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
