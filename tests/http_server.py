"""Local HTTP test server for webget tests and benchmarks.

Stdlib-only (http.server in a thread). Produces deterministic responses:
normal pages, login pages, auth failures, redirects, challenge pages,
slow/malformed/large/binary/compressed responses, cookie-gated pages, and
SSRF bait endpoints (redirect into 127.0.0.1, private-looking hosts).

Usage (conftest fixture):
    from tests.http_server import TestServer
    server = TestServer()
    server.start()
    try:
        ... use server.url("/normal") ...
    finally:
        server.stop()

Benchmark usage:
    python -m tests.http_server --bench  (prints the URL inventory)
"""
from __future__ import annotations

import gzip
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE_HTML = """<!DOCTYPE html>
<html><head><title>{title}</title></head>
<body><h1>{title}</h1><p>{body}</p></body></html>"""

LONG_BODY = (
    "The quick brown fox jumps over the lazy dog. " * 40
)  # ~1.4KB, well above the 100-char content threshold


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # Server-wide counters for concurrency tests (instance attribute set at bind).
    active = 0
    max_active = 0
    hits = 0
    lock = threading.Lock()

    def log_message(self, *args):  # silence
        pass

    def _track(self):
        with _Handler.lock:
            _Handler.hits += 1
            _Handler.active += 1
            _Handler.max_active = max(_Handler.max_active, _Handler.active)

    def _untrack(self):
        with _Handler.lock:
            _Handler.active -= 1

    def _send(self, code, body=b"", ctype="text/html", headers=None, raw=False):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if raw or body:
            self.wfile.write(body)

    def _page(self, code=200, title="Test", body="", headers=None):
        self._send(code, PAGE_HTML.format(title=title, body=body).encode(), headers=headers or {})

    def _route(self):
        self._track()
        try:
            path = self.path.split("?", 1)[0]
            q = {}
            if "?" in self.path:
                for part in self.path.split("?", 1)[1].split("&"):
                    if "=" in part:
                        k, v = part.split("=", 1)
                        q[k] = v

            # --- concurrency probe: /slow sleeps, /concurrency records max ---
            if path == "/concurrency":
                with _Handler.lock:
                    cur = _Handler.active
                time.sleep(0.05)
                self._page(title="Concurrency probe", body=f"active={cur} " + LONG_BODY)
                return
            if path == "/slow":
                time.sleep(float(q.get("sec", "0.3")))
                self._page(title="Slow page", body="finally done")
                return
            if path == "/timeout":
                time.sleep(float(q.get("sec", "10")))
                self._page(title="Too late", body="should not be seen")
                return

            # --- normal content ---
            if path == "/":
                self._page(title="Home", body=LONG_BODY)
                return
            if path == "/normal":
                self._page(title="Normal", body=LONG_BODY)
                return
            if path == "/title-only":
                self._send(200, b"<html><head><title>Title Only</title></head><body></body></html>")
                return
            if path == "/thin":
                self._page(title="Thin", body="short")
                return
            if path == "/long":
                self._page(title="Long", body=LONG_BODY * int(q.get("n", "10")))
                return

            # --- auth states ---
            if path == "/login":
                self._page(
                    200,
                    title="Log in",
                    body='<form><input type="text"/><input type="password"/></form>',
                )
                return
            if path == "/sion-login":
                self._page(
                    200,
                    title="SION",
                    body="SION ITB STIKOM Bali NIM Mahasiswa Password Lupa password Show Password",
                )
                return
            if path == "/401":
                self._send(401, b"Unauthorized")
                return
            if path == "/403":
                self._send(403, b"Forbidden")
                return
            if path == "/403-login":
                self._page(403, title="Forbidden", body="Please log in to continue")
                return
            if path == "/429":
                self._send(429, b"Too Many Requests")
                return
            if path == "/500":
                self._send(500, b"Internal Server Error")
                return
            if path == "/challenge":
                self._page(200, title="Just a moment...", body="cf-chl-opt captcha verify you are human")
                return
            if path == "/denied":
                self._page(200, title="Denied", body="Access denied unusual traffic")
                return

            # --- cookie-gated page ---
            if path == "/cookie-gated":
                if self.headers.get("Cookie"):
                    self._page(title="Secret", body="you are authenticated " + LONG_BODY)
                else:
                    self._send(403, b"Forbidden")
                return

            # --- redirects ---
            if path == "/redirect":
                self._send(302, headers={"Location": "/normal"})
                return
            if path == "/redirect-301":
                self._send(301, headers={"Location": "/normal"})
                return
            if path == "/redirect-307":
                self._send(307, headers={"Location": "/normal"})
                return
            if path == "/redirect-308":
                self._send(308, headers={"Location": "/normal"})
                return
            if path == "/redirect-loop":
                self._send(302, headers={"Location": "/redirect-loop"})
                return
            if path == "/redirect-chain":
                n = int(q.get("n", "3"))
                self._send(302, headers={"Location": f"/redirect-chain?n={n - 1}" if n > 1 else "/normal"})
                return
            if path == "/redirect-external":
                target = q.get("to", "https://example.com/")
                self._send(302, headers={"Location": target})
                return
            if path == "/redirect-private":
                # SSRF bait: public-looking endpoint redirecting into loopback.
                port = self.server.server_address[1]
                self._send(302, headers={"Location": f"http://127.0.0.1:{port}/private"})
                return
            if path == "/redirect-private-page":
                # Same bait but landing on a crawl4ai-extractable page.
                port = self.server.server_address[1]
                self._send(302, headers={"Location": f"http://127.0.0.1:{port}/private-page"})
                return

            # --- SSRF bait endpoints (they must never be reachable from a
            #     properly-guarded client) ---
            if path == "/private":
                self._send(200, b"PRIVATE DATA LEAKED " + LONG_BODY.encode())
                return
            if path == "/private-page":
                # Normal HTML page (crawl4ai-extractable) that only exists on
                # loopback: the SSRF probe target for browser-strategy tests.
                self._page(title="Private Page", body="PRIVATE DATA LEAKED " + LONG_BODY)
                return
            if path == "/metadata":
                self._send(200, b"role: arn:aws:iam::123456789012:role/fake")
                return

            # --- malformed / empty / binary / compressed ---
            if path == "/empty":
                self._send(200, b"")
                return
            if path == "/malformed":
                self._send(200, b"<html><head><title>Broken</title><body><div>no closing tags")
                return
            if path == "/binary":
                self._send(200, bytes(range(256)) * 4, ctype="application/octet-stream")
                return
            if path == "/pdf":
                self._send(200, b"%PDF-1.4 fake pdf body", ctype="application/pdf")
                return
            if path == "/json":
                self._send(200, json.dumps({"hello": "world"}).encode(), ctype="application/json")
                return
            if path == "/gzip":
                body = gzip.compress(PAGE_HTML.format(title="Gzipped", body=LONG_BODY).encode())
                self._send(200, body, ctype="text/html", headers={"Content-Encoding": "gzip"})
                return
            if path == "/huge":
                # ~5MB body; streamed in chunks to avoid building one giant bytes.
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(5 * 1024 * 1024))
                self.end_headers()
                chunk = b"x" * 65536
                total = 5 * 1024 * 1024
                sent = 0
                while sent < total:
                    self.wfile.write(chunk[: min(len(chunk), total - sent)])
                    sent += len(chunk[: min(len(chunk), total - sent)])
                return
            if path == "/oversize":
                # 30MB body, over MAX_RESPONSE_BYTES (25MB): the client must
                # abort while streaming, before buffering the whole thing.
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(30 * 1024 * 1024))
                self.end_headers()
                chunk = b"y" * 65536
                total = 30 * 1024 * 1024
                sent = 0
                try:
                    while sent < total:
                        self.wfile.write(chunk[: min(len(chunk), total - sent)])
                        sent += len(chunk[: min(len(chunk), total - sent)])
                except (BrokenPipeError, ConnectionResetError):
                    pass  # client aborted mid-stream: expected
                return

            # --- bench inventory ---
            if path == "/bench-list":
                self._send(200, json.dumps(self.server.bench_urls).encode(), ctype="application/json")
                return

            self._send(404, b"Not Found")
        finally:
            self._untrack()

    do_GET = _route
    do_POST = _route
    do_HEAD = _route


class TestServer:
    """Threaded HTTP server on 127.0.0.1 with a deterministic URL inventory."""

    def __init__(self, host="127.0.0.1", port=0):
        self.host = host
        self.port = port
        self._httpd = None
        self._thread = None
        # URL inventory for the ladder: success, failure, auth, redirect, etc.
        self.bench_urls = [
            "/normal", "/long?n=2", "/title-only", "/redirect", "/gzip",
            "/login", "/403", "/429", "/500", "/thin", "/challenge", "/denied",
            "/slow?sec=0.05", "/empty", "/malformed", "/json", "/binary",
        ]

    def start(self):
        self._httpd = ThreadingHTTPServer((self.host, self.port), _Handler)
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    def url(self, path):
        return f"http://{self.host}:{self.port}{path}"

    def full_url(self, path):
        """Fully-qualified URL that the private-IP guard would see (same thing)."""
        return self.url(path)

    def reset_counters(self):
        with _Handler.lock:
            _Handler.hits = 0
            _Handler.max_active = 0
            _Handler.active = 0

    @property
    def max_active(self):
        with _Handler.lock:
            return _Handler.max_active

    @property
    def hits(self):
        with _Handler.lock:
            return _Handler.hits


if __name__ == "__main__":
    srv = TestServer().start()
    print(f"TestServer on {srv.host}:{srv.port}")
    for p in srv.bench_urls:
        print(f"  {srv.url(p)}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        srv.stop()
