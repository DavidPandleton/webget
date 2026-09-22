"""Tes discovery: SSRF tidak boleh bocor lewat hop redirect.

Ditemukan saat mengaudit discovery.py. `discover_urls` memakai

    httpx.AsyncClient(timeout=timeout, follow_redirects=True)

`follow_redirects=True` membuat httpx mengikuti redirect DI DALAM
client.get(). Guard SSRF hanya memeriksa URL awal dan kandidat sitemap
SEBELUM pemanggilan, jadi hop redirect tidak pernah dinilai. Domain
publik yang membalas 302 ke 127.0.0.1 atau 169.254.169.254 (cloud
metadata) akan diikuti, dan isinya bisa masuk ke hasil discovery.

Dibuktikan sebelum perbaikan: server uji mengirim 302 dari /sitemap.xml
ke /metadata-rahasia, dan /metadata-rahasia BENAR-BENAR dipukul.

http.py sudah benar (fetch_http memakai follow_redirects=False dan
ssrf_guard tiap hop); discovery.py menyamakannya.
"""

from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

import pytest

from webget import discovery


class _Handler(BaseHTTPRequestHandler):
    dipukul: ClassVar[list] = []

    def do_GET(self):
        type(self).dipukul.append(self.path)
        port = self.server.server_port
        if self.path == "/sitemap.xml":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{port}/metadata-rahasia")
            self.end_headers()
            return
        if self.path == "/metadata-rahasia":
            self.send_response(200)
            self.send_header("Content-Type", "application/xml")
            self.end_headers()
            self.wfile.write(
                b"<?xml version='1.0'?><urlset><url>"
                b"<loc>http://rahasia.internal/x</loc></url></urlset>"
            )
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *a):
        pass


@pytest.fixture
def server():
    _Handler.dipukul = []
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()


class TestRedirectTidakBocor:
    def test_hop_redirect_ke_privat_tidak_diikuti(self, server, monkeypatch):
        """Inti temuan: 302 ke 127.0.0.1 tidak boleh diikuti.

        URL awal dianggap publik (seperti domain nyata) supaya pre-check
        pada URL awal tidak menutupi celahnya; hop redirect tetap dinilai
        dengan kebijakan sebenarnya.
        """
        port = server.server_port
        target = f"http://127.0.0.1:{port}/"
        asli = discovery._is_private_target

        def tiruan(url, allow_private=None):
            # Anggap URL awal publik; hop lain dinilai apa adanya.
            if url.rstrip("/") == target.rstrip("/"):
                return False
            return asli(url, allow_private=allow_private)

        monkeypatch.setattr(discovery, "_is_private_target", tiruan)

        hasil = asyncio.run(
            discovery.discover_urls(target, limit=10, timeout=5, allow_private=False)
        )

        assert "/metadata-rahasia" not in _Handler.dipukul, (
            f"hop redirect ke alamat privat DIIKUTI; jalur dipukul: "
            f"{_Handler.dipukul}"
        )
        assert hasil == [], "hasil tidak boleh berisi data dari alamat privat"

    def test_kebocoran_benar_benar_tertutup_pada_data(self, server, monkeypatch):
        """Hasil discovery tidak boleh memuat URL dari hop yang diblokir."""
        port = server.server_port
        target = f"http://127.0.0.1:{port}/"
        asli = discovery._is_private_target
        monkeypatch.setattr(
            discovery,
            "_is_private_target",
            lambda url, allow_private=None: (
                False
                if url.rstrip("/") == target.rstrip("/")
                else asli(url, allow_private=allow_private)
            ),
        )
        hasil = asyncio.run(
            discovery.discover_urls(target, limit=10, timeout=5, allow_private=False)
        )
        assert not any("rahasia.internal" in u for u in hasil)


class TestPerilakuNormalTetap:
    def test_sitemap_normal_masih_dibaca(self, server, monkeypatch):
        """Tanpa redirect, sitemap biasa tetap menghasilkan URL."""
        class HandlerNormal(_Handler):
            def do_GET(self):
                type(self).dipukul.append(self.path)
                if self.path.endswith(".xml"):
                    self.send_response(200)
                    self.send_header("Content-Type", "application/xml")
                    self.end_headers()
                    self.wfile.write(
                        b"<?xml version='1.0'?><urlset><url>"
                        b"<loc>https://contoh.test/halaman</loc></url></urlset>"
                    )
                    return
                self.send_response(404)
                self.end_headers()

        srv2 = HTTPServer(("127.0.0.1", 0), HandlerNormal)
        threading.Thread(target=srv2.serve_forever, daemon=True).start()
        try:
            target2 = f"http://127.0.0.1:{srv2.server_port}/"
            hasil = asyncio.run(
                discovery.discover_urls(target2, limit=10, timeout=5, allow_private=True)
            )
            assert "https://contoh.test/halaman" in hasil
        finally:
            srv2.shutdown()
