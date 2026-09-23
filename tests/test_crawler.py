import asyncio
import json

import pytest

from webget import crawler
from webget.frontier import CrawlFrontier


def test_crawl_site_resumes_frontier_and_exports(tmp_path, monkeypatch):
    seed = "https://example.com/"
    child = "https://example.com/child"

    async def fake_scrape(urls, **_kwargs):
        return {url: {"status": "success", "markdown": "ok"} for url in urls}

    async def fake_discover(url, **_kwargs):
        return [child] if url == seed else []

    monkeypatch.setattr(crawler, "scrape_many", fake_scrape)
    monkeypatch.setattr(crawler, "discover_urls", fake_discover)
    db = tmp_path / "frontier.sqlite3"
    jsonl = tmp_path / "crawl.jsonl"
    markdown = tmp_path / "crawl.md"

    out = asyncio.run(
        crawler.crawl_site(
            seed,
            db,
            max_pages=2,
            max_depth=1,
            output_jsonl=jsonl,
            output_markdown=markdown,
        )
    )

    assert out["stats"]["done"] == 2
    rows = [json.loads(line) for line in jsonl.read_text().splitlines()]
    assert [row["url"] for row in rows] == [seed, child]
    assert rows[0]["markdown"] == "ok"
    assert "ok" in markdown.read_text()


def test_crawl_site_records_failures(monkeypatch, tmp_path):
    async def fake_scrape(urls, **_kwargs):
        return {urls[0]: {"status": "error", "error": "blocked"}}

    monkeypatch.setattr(crawler, "scrape_many", fake_scrape)
    monkeypatch.setattr(crawler, "discover_urls", lambda *a, **k: pytest.fail("must not discover after failure"))

    out = asyncio.run(crawler.crawl_site("https://example.com/", tmp_path / "f.db", max_pages=1))
    assert out["stats"]["failed"] == 1
    assert out["results"][0]["error"] == "blocked"


def test_reopen_recovers_in_progress_claim(tmp_path):
    path = tmp_path / "recover.db"
    with CrawlFrontier(path) as frontier:
        frontier.enqueue("https://example.com/")
        assert frontier.claim()
    with CrawlFrontier(path) as reopened:
        assert reopened.claim()[0].url == "https://example.com/"
