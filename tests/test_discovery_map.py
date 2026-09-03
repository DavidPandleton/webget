import asyncio

from webget.discovery import _extract_sitemap_urls, discover_urls


def test_extract_sitemap_urls_standard_xml():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <url>
            <loc>https://example.com/page1</loc>
        </url>
        <url>
            <loc>https://example.com/page2</loc>
        </url>
    </urlset>
    """
    urls = _extract_sitemap_urls(xml)
    assert urls == ["https://example.com/page1", "https://example.com/page2"]


def test_extract_sitemap_urls_malformed_regex_fallback():
    xml = """<urlset><url><loc>https://example.com/broken1</loc></unclosed>"""
    urls = _extract_sitemap_urls(xml)
    assert "https://example.com/broken1" in urls


def test_discover_urls_private_target_blocked():
    urls = asyncio.run(discover_urls("http://127.0.0.1/sitemap.xml", allow_private=False))
    assert urls == []
