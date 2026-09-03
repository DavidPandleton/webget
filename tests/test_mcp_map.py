import asyncio
from unittest.mock import AsyncMock, patch

from webget_mcp import map as mcp_map


def test_mcp_map_clamps_limit():
    res = asyncio.run(mcp_map("https://example.com", limit=-5))
    assert len(res) == 1
    assert "error: limit must be between" in res[0]


def test_mcp_map_calls_discover_urls():
    with patch("webget_cli.discover_urls", new_callable=AsyncMock) as mock_disc:
        mock_disc.return_value = ["https://example.com/p1", "https://example.com/p2"]
        res = asyncio.run(mcp_map("https://example.com", limit=50))
        assert res == ["https://example.com/p1", "https://example.com/p2"]
        mock_disc.assert_awaited_once_with("https://example.com", limit=50, timeout=15)
