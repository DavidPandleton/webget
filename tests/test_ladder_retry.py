import asyncio
from unittest.mock import patch

from webget.ladder import scrape_many


def test_scrape_many_retry_transient_timeout(fresh_cache, server):
    calls = []

    async def mock_fetch(url, *args, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            raise TimeoutError("timeout")
        return {
            "title": "Success After Retry",
            "markdown": "Valid content length " * 10,
            "status": "success",
        }

    async def run_without_retry():
        return await scrape_many(
            [server.url("/test")], strategy="http", per_url_timeout=1, retry_transient=False
        )

    async def run_with_retry():
        return await scrape_many(
            [server.url("/test")], strategy="http", per_url_timeout=1, retry_transient=True
        )

    with patch("webget.ladder._resolve_fetch_http", return_value=mock_fetch):
        # Without retry flag -> error on first timeout
        res = asyncio.run(run_without_retry())
        target = server.url("/test")
        assert res[target]["status"] == "error"
        assert res[target]["attempts"] == 1

        # With retry_transient=True -> retries and succeeds on 2nd attempt
        calls.clear()
        res = asyncio.run(run_with_retry())
        assert res[target]["status"] == "success"
        assert res[target]["attempts"] == 2
