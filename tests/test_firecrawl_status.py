"""Tes firecrawl: jangan mengarang status halaman target.

Ditemukan saat mengaudit firecrawl.py. Fetch melaporkan

    "status_code": target_status if target_status is not None else r.status_code

`r.status_code` adalah status transport ke api.firecrawl.dev, dan baris
di atasnya sudah menolak apa pun selain 200 - jadi fallback itu SELALU
200. Pemanggil menerima status 200 untuk halaman yang statusnya tidak
diketahui Firecrawl. 200 itu tampak seperti status halaman target (dan
tampak seperti sukses), padahal hanya menandakan bahwa permintaan ke
Firecrawl sendiri berhasil.

Komentar di fungsi yang sama sudah mengakui nilai itu "told callers
nothing", tetapi tetap dilaporkan.

Kontrak yang benar sudah ada di profile._auth_state, yang memakai
`if status and status >= 400:` - artinya "kalau status TAHU dan >= 400".
Mengirim 200 karangan justru membohongi kode itu.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from webget import firecrawl


class _Respons:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = {} if payload is None else payload
        self.text = text

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _jalankan(payload, status=200, text="", max_chars=10000):
    """Panggil fetch_firecrawl dengan httpx.AsyncClient dipalsukan."""
    respons = _Respons(status, payload, text)
    asli = httpx.AsyncClient

    class ClientPalsu:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return respons

    httpx.AsyncClient = ClientPalsu
    try:
        return asyncio.run(firecrawl.fetch_firecrawl("https://t.test/", max_chars, "k"))
    finally:
        httpx.AsyncClient = asli


def _jalankan_dengan_max(payload, max_chars, status=200, text=""):
    return _jalankan(payload, status=status, text=text, max_chars=max_chars)


class TestStatusTidakDiketahui:
    def test_tanpa_statuscode_tidak_mengarang_200(self):
        """Inti temuan: tidak ada statusCode -> status_code None, bukan 200."""
        hasil = _jalankan({"data": {"markdown": "isi", "metadata": {"title": "T"}}})
        assert hasil["status_code"] is None, (
            f"melaporkan {hasil['status_code']!r} padahal Firecrawl tidak "
            "mengirim status halaman; 200 di sini adalah status transport ke "
            "Firecrawl, bukan status halaman target"
        )

    def test_statuscode_infinity_tidak_jadi_200(self):
        """json.loads menerima Infinity; itu harus jadi None, bukan 200."""
        payload = json.loads('{"data":{"markdown":"y","metadata":{"statusCode":Infinity}}}')
        hasil = _jalankan(payload)
        assert hasil["status_code"] is None

    def test_statuscode_di_luar_rentang_tidak_jadi_200(self):
        """999 bukan status HTTP valid; jangan jatuh ke 200 transport."""
        hasil = _jalankan({"data": {"markdown": "z", "metadata": {"statusCode": 999}}})
        assert hasil["status_code"] is None


class TestStatusDiketahuiTetapDilaporkan:
    def test_404_dilaporkan(self):
        hasil = _jalankan({"data": {"markdown": "err", "metadata": {"statusCode": 404}}})
        assert hasil["status_code"] == 404

    def test_string_numerik_dikoersi(self):
        hasil = _jalankan({"data": {"markdown": "x", "metadata": {"statusCode": "403"}}})
        assert hasil["status_code"] == 403

    def test_200_asli_tetap_200(self):
        """Kalau Firecrawl MEMANG bilang 200, laporkan 200."""
        hasil = _jalankan({"data": {"markdown": "ok", "metadata": {"statusCode": 200}}})
        assert hasil["status_code"] == 200


class TestPerilakuLainTidakBerubah:
    def test_transport_bukan_200_dilempar(self):
        with pytest.raises(RuntimeError) as exc:
            _jalankan({"error": "x"}, status=429, text="rate limited")
        assert "429" in str(exc.value)

    def test_markdown_kosong_dengan_status_target_disebut(self):
        with pytest.raises(RuntimeError) as exc:
            _jalankan({"data": {"markdown": "", "metadata": {"statusCode": 403}}})
        assert "403" in str(exc.value)

    def test_markdown_kosong_tanpa_status(self):
        with pytest.raises(RuntimeError):
            _jalankan({"data": {"markdown": "", "metadata": {}}})

    def test_data_list_tidak_meledak(self):
        with pytest.raises(RuntimeError):
            _jalankan({"data": [1, 2, 3]})

    def test_markdown_dipotong(self):
        """max_chars harus lebih kecil dari isi supaya benar-benar menguji.

        Versi pertama tes ini memakai max_chars=10000 dengan isi 5000
        karakter, jadi tidak ada yang dipotong dan tes gagal - asumsinya
        salah, bukan kodenya. Sekarang max_chars kecil.
        """
        panjang = "a" * 5000
        hasil = _jalankan_dengan_max({"data": {"markdown": panjang,
                                               "metadata": {"statusCode": 200}}}, 500)
        assert len(hasil["markdown"]) < len(panjang)
