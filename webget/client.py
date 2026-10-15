"""High-level Python API for webget."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from .cache import parse_cookie_file, parse_headers
from .ladder import scrape_many


@dataclass
class FetchResult:
    """Represents the outcome of a webget fetch operation."""

    url: str
    status: str
    title: str = ""
    markdown: str = ""
    method: str = ""
    auth: dict[str, Any] | None = None
    error: str | None = None
    metadata: dict[str, Any] | None = None
    cached: bool = False
    raw: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.status == "success"


class Client:
    """Client for fetching web content with optional persistent profile and cookies."""

    def __init__(
        self,
        profile: str | None = None,
        cookies_file: str | None = None,
        headers: list[str] | dict[str, str] | None = None,
        timeout: int = 20,
        strategy: str = "auto",
        no_cache: bool = False,
    ):
        self.profile = profile
        self.cookies = parse_cookie_file(cookies_file) if cookies_file else None
        if isinstance(headers, dict):
            self.headers = headers
        elif isinstance(headers, list):
            self.headers = parse_headers(headers)
        else:
            self.headers = {}
        self.timeout = timeout
        self.strategy = strategy
        self.no_cache = no_cache

    def fetch(
        self,
        url: str,
        max_chars: int = 50000,
        fresh: bool = False,
        timeout: int | None = None,
    ) -> FetchResult:
        """Fetch a single URL synchronously."""
        results = self.fetch_many([url], max_chars=max_chars, fresh=fresh, timeout=timeout)
        return results.get(
            url, FetchResult(url=url, status="error", error="missing result in response")
        )

    def fetch_many(
        self,
        urls: list[str],
        max_chars: int = 50000,
        fresh: bool = False,
        timeout: int | None = None,
        concurrency: int = 10,
    ) -> dict[str, FetchResult]:
        """Fetch multiple URLs synchronously."""
        return asyncio.run(
            self.fetch_many_async(
                urls, max_chars=max_chars, fresh=fresh, timeout=timeout, concurrency=concurrency
            )
        )

    async def fetch_many_async(
        self,
        urls: list[str],
        max_chars: int = 50000,
        fresh: bool = False,
        timeout: int | None = None,
        concurrency: int = 10,
    ) -> dict[str, FetchResult]:
        """Fetch multiple URLs asynchronously."""
        res_dict = await scrape_many(
            urls,
            max_chars=max_chars,
            per_url_timeout=timeout or self.timeout,
            cookies=self.cookies,
            headers=self.headers,
            fresh=fresh,
            strategy=self.strategy,
            profile=self.profile,
            no_cache=self.no_cache,
            max_concurrency=concurrency,
        )
        out = {}
        for u, d in res_dict.items():
            out[u] = FetchResult(
                url=u,
                status=d.get("status", "error"),
                title=d.get("title", ""),
                markdown=d.get("markdown", ""),
                method=d.get("method", ""),
                auth=d.get("auth"),
                error=d.get("error"),
                metadata=d.get("metadata"),
                cached=d.get("cached", False),
                raw=d,
            )
        return out


def fetch(
    url: str,
    profile: str | None = None,
    cookies_file: str | None = None,
    max_chars: int = 50000,
    fresh: bool = False,
    timeout: int = 20,
) -> FetchResult:
    """Convenience function to fetch a single URL."""
    c = Client(profile=profile, cookies_file=cookies_file, timeout=timeout)
    return c.fetch(url, max_chars=max_chars, fresh=fresh)
