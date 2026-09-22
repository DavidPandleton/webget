"""Tes discovery: halaman biasa dengan 'sitemap' di path bukan sub-sitemap.

discover_urls memutuskan apakah URL hasil ekstraksi sitemap itu SUB-SITEMAP
(harus di-GET dan di-parse lagi) atau URL BIASA (langsung masuk hasil):

    (u.endswith(".xml") or "sitemap" in u)  -> sub-sitemap
    else                                    -> discovered

"sitemap" in u terlalu longgar. Halaman HTML biasa yang path-nya memuat
"sitemap" - mis. https://situs/halaman/sitemap-guide/ - disangka
sub-sitemap, sehingga:

  * URL sah itu HILANG dari hasil (masuk antrean, bukan discovered), dan
  * satu permintaan HTTP terbuang untuk HTML yang pasti gagal di-parse
    sebagai XML.

Tes di sini memakai server lokal supaya perilakunya diuji end-to-end, bukan
lewat literal string di sumber.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from webget.discovery import discover_urls

SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>{base}/halaman/sitemap-guide/</loc></url>
  <url><loc>{base}/tentang</loc></url>
  <url><loc>{base}/kontak</loc></url>
  <url><loc>{base}/sub-sitemap.xml</loc></url>
</urlset>
"""


def _handler_factory(requested):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            requested.append(self.path)
            if self.path == "/sitemap.xml":
                base = f"http://{self.headers['Host']}"
                body = SITEMAP.format(base=base).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/xml")
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/robots.txt":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"User-agent: *\nAllow: /\n")
            elif self.path == "/sub-sitemap.xml":
                base = f"http://{self.headers['Host']}"
                body = (
                    f'<?xml version="1.0"?><urlset>'
                    f"<url><loc>{base}/dari-sub</loc></url></urlset>"
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/xml")
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *a):
            pass

    return H


@pytest.fixture
def server():
    requested = []
    srv = HTTPServer(("127.0.0.1", 0), _handler_factory(requested))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", requested
    srv.shutdown()


def _discover(server, limit=100):
    base, _ = server
    import asyncio

    return asyncio.run(
        discover_urls(base, limit=limit, timeout=10, allow_private=True)
    )


class TestHalamanSitemapBukanSubSitemap:
    def test_halaman_dengan_sitemap_di_path_masuk_hasil(self, server):
        """'/halaman/sitemap-guide/' harus muncul sebagai URL biasa."""
        hasil = _discover(server)
        assert any("sitemap-guide" in u for u in hasil), (
            f"halaman dengan 'sitemap' di path hilang dari hasil: {hasil}"
        )

    def test_halaman_sitemap_tidak_di_get_ulang(self, server):
        """Halaman itu tidak boleh diminta sebagai XML."""
        _, requested = server
        _discover(server)
        assert "/halaman/sitemap-guide/" not in requested, (
            f"discovery meminta ulang halaman bukan-XML sebagai sitemap: "
            f"{requested}"
        )

    def test_url_biasa_lain_masuk_hasil(self, server):
        hasil = _discover(server)
        assert any(u.endswith("/tentang") for u in hasil)
        assert any(u.endswith("/kontak") for u in hasil)


class TestSubSitemapAsliTetapDiikuti:
    def test_sub_sitemap_xml_diambil(self, server):
        """Sub-sitemap .xml asli harus tetap diikuti dan isinya masuk."""
        hasil = _discover(server)
        assert any("dari-sub" in u for u in hasil), (
            f"isi sub-sitemap tidak masuk hasil: {hasil}"
        )

    def test_sub_sitemap_di_get(self, server):
        _, requested = server
        _discover(server)
        assert "/sub-sitemap.xml" in requested


class TestBatas:
    def test_limit_dihormati(self, server):
        hasil = _discover(server, limit=2)
        assert len(hasil) <= 2
