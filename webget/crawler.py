"""Bounded, resumable single-process crawl orchestration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .discovery import discover_urls
from .frontier import CrawlFrontier
from .ladder import scrape_many


async def crawl_site(
    seed_url: str,
    frontier_path: str | Path,
    *,
    max_pages: int = 100,
    max_depth: int = 1,
    discovery_limit: int = 100,
    timeout: int = 20,
    strategy: str = "auto",
    output_jsonl: str | Path | None = None,
    output_markdown: str | Path | None = None,
) -> dict[str, Any]:
    """Crawl a bounded site frontier and return aggregate status/results.

    The function is intentionally orchestration-only: URL policy and durable
    state live in ``CrawlFrontier`` while fetching remains ``scrape_many``.
    Reopening the same frontier resumes pending work after a clean stop or
    process crash.
    """
    if not isinstance(max_pages, int) or max_pages < 1:
        raise ValueError("max_pages must be a positive integer")
    if not isinstance(max_depth, int) or max_depth < 0:
        raise ValueError("max_depth must be a non-negative integer")
    seed = seed_url
    with CrawlFrontier(frontier_path, allowed_domains={_host(seed)}, max_depth=max_depth, max_pages=max_pages) as frontier:
        frontier.enqueue(seed)
        while True:
            batch = frontier.claim(min(10, max_pages))
            if not batch:
                break
            urls = [item.url for item in batch]
            scraped = await scrape_many(urls, per_url_timeout=timeout, strategy=strategy)
            for item in batch:
                result = scraped.get(item.url, {"status": "error", "error": "missing result"})
                if result.get("status") == "success":
                    frontier.complete(item.url)
                    if item.depth < max_depth:
                        for child in await discover_urls(item.url, limit=discovery_limit, timeout=timeout):
                            frontier.enqueue(child, depth=item.depth + 1, parent_url=item.url)
                else:
                    frontier.fail(item.url, result.get("error") or result.get("status", "error"))
            if frontier.stats()["done"] + frontier.stats()["failed"] >= max_pages:
                break
        stats = frontier.stats()
        results = _read_results(frontier_path)

    if output_jsonl:
        _write_jsonl(output_jsonl, results)
    if output_markdown:
        _write_markdown(output_markdown, results)
    return {"stats": stats, "results": results}


def _host(url: str) -> str:
    from urllib.parse import urlsplit

    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    if not host:
        raise ValueError(f"seed URL has no hostname: {url!r}")
    return host


def _read_results(frontier_path: str | Path) -> list[dict[str, Any]]:
    import sqlite3

    db = sqlite3.connect(str(frontier_path))
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute(
            "SELECT url, depth, parent_url, status, attempts, error "
            "FROM frontier ORDER BY depth, id"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        db.close()


def _write_jsonl(path: str | Path, results: list[dict[str, Any]]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in results))


def _write_markdown(path: str | Path, results: list[dict[str, Any]]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Crawl results", ""]
    for row in results:
        lines.append(f"- [{row['status']}]({row['url']}) depth={row['depth']}")
        if row.get("error"):
            lines.append(f"  - Error: {row['error']}")
    target.write_text("\n".join(lines) + "\n")
