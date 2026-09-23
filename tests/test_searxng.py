import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from webget.search import SearxngSearchProvider, search_with_provenance


class _SearxHandler(BaseHTTPRequestHandler):
    payload: ClassVar[dict] = {"results": [{"title": "A", "url": "https://a.example", "content": "alpha"}]}
    status: ClassVar[int] = 200

    def do_GET(self):
        assert urlparse(self.path).path == "/search"
        assert parse_qs(urlparse(self.path).query)["format"] == ["json"]
        body = json.dumps(self.payload).encode()
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


@pytest.fixture
def searx_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SearxHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_searxng_adapter_normalizes_results(searx_server):
    provider = SearxngSearchProvider(searx_server)
    assert provider.known_engines() == ["searxng"]
    assert provider.text("hello", 1) == [
        {"title": "A", "href": "https://a.example", "body": "alpha"}
    ]


def test_searxng_adapter_integrates_with_provenance(searx_server):
    results, provenance = search_with_provenance(
        "hello", n=1, engine="searxng", provider=SearxngSearchProvider(searx_server)
    )
    assert results[0]["url"] == "https://a.example"
    assert provenance["engine"] == "searxng"
    assert provenance["failed_over"] is False


def test_searxng_http_error(searx_server):
    _SearxHandler.status = 503
    try:
        with pytest.raises(httpx.HTTPStatusError):
            SearxngSearchProvider(searx_server).text("hello", 1)
    finally:
        _SearxHandler.status = 200


def test_searxng_requires_endpoint(monkeypatch):
    monkeypatch.delenv("WEBGET_SEARXNG_URL", raising=False)
    with pytest.raises(ValueError, match="endpoint is required"):
        SearxngSearchProvider()


def test_searxng_rejects_malformed_payload(searx_server):
    _SearxHandler.payload = {"unexpected": []}
    try:
        with pytest.raises(TypeError, match="no results list"):
            SearxngSearchProvider(searx_server).text("hello", 1)
    finally:
        _SearxHandler.payload = {"results": [{"title": "A", "url": "https://a.example", "content": "alpha"}]}
