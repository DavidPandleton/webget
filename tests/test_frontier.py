from webget.frontier import CrawlFrontier, normalize_url


def test_normalize_url_removes_fragment_and_default_port():
    assert normalize_url("HTTPS://Example.COM:443/a#section") == "https://example.com/a"


def test_frontier_deduplicates_and_resumes(tmp_path):
    db = tmp_path / "crawl.sqlite3"
    with CrawlFrontier(db, max_depth=1, max_pages=3) as frontier:
        assert frontier.enqueue("https://example.com/")
        assert not frontier.enqueue("https://EXAMPLE.com/#top")
        assert frontier.enqueue("https://example.com/child", depth=1, parent_url="https://example.com/")
        assert not frontier.enqueue("https://example.com/deep", depth=2)
        first = frontier.claim(1)
        assert first[0].url == "https://example.com/"
        frontier.complete(first[0].url)

    with CrawlFrontier(db, max_depth=1, max_pages=3) as resumed:
        second = resumed.claim(1)
        assert second[0].url == "https://example.com/child"
        assert second[0].attempts == 1
        resumed.fail(second[0].url, "temporary")
        resumed.retry(second[0].url)
        retried = resumed.claim(1)
        assert retried[0].attempts == 2
        assert resumed.stats() == {"pending": 0, "in_progress": 1, "done": 1, "failed": 0, "total": 2}


def test_frontier_domain_and_page_budget(tmp_path):
    with CrawlFrontier(tmp_path / "frontier.db", allowed_domains={"example.com"}, max_pages=1) as frontier:
        assert frontier.enqueue("https://example.com/a")
        assert not frontier.enqueue("https://other.example/b")
        assert len(frontier.claim(10)) == 1
        assert frontier.enqueue("https://example.com/c")
        assert frontier.claim(10) == []


def test_stale_lease_is_recovered(tmp_path):
    path = tmp_path / "stale.db"
    with CrawlFrontier(path, lease_timeout=1) as frontier:
        frontier.enqueue("https://example.com/")
        item = frontier.claim()[0]
        frontier._db.execute(
            "UPDATE frontier SET updated_at=unixepoch('subsec') - 10 WHERE id=?", (item.id,)
        )
    with CrawlFrontier(path, lease_timeout=1) as reopened:
        assert reopened.claim()[0].url == "https://example.com/"
