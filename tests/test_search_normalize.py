"""Tes search: satu baris hasil cacat tidak boleh menggagalkan pencarian.

Ditemukan saat mengaudit search.py. Normalisasi hasil berbunyi:

    {"title": r["title"], "url": r["href"], "snippet": r.get("body", "")}

`body` dilindungi .get(), tetapi `title` dan `href` diakses langsung.
Satu baris tanpa "title" - dari engine yang sehat, yang mengembalikan 14
baris lain dengan benar - melempar KeyError dan menggagalkan SELURUH
pencarian. Provider memang mengirim baris tidak lengkap, karena ddgs
memetakan beberapa backend dengan bentuk yang berbeda.

Baris tanpa "href" dilewati: tanpa URL, hasilnya tidak berguna bagi
pemanggil karena tidak bisa dibuka. Baris tanpa "title" dipertahankan
dengan judul kosong, karena URL-nya masih berguna.

CATATAN KOREKSI: saat mengaudit, gue sempat mengklaim engine sehat juga
dicatat GAGAL di health ledger. Itu SALAH - _record dipanggil SEBELUM
normalisasi dengan bool(rows), dan rows tidak kosong, jadi dicatat sukses
(ok=1.0). KeyError terjadi setelah _record. Klaim itu dicabut.

CATATAN KEDUA: versi pertama tes ini memasang ddgs palsu lewat monkeypatch
per-tes, tetapi search_with_provenance mengimpor `from ddgs import DDGS`
DI DALAM fungsi. Versi palsu harus ada di sys.modules SEBELUM panggilan,
dan pencarian sungguhan sempat jalan (hasil dari grokipedia muncul di
assertion). Fixture di bawah memasang sekali per tes dan menggagalkan
permintaan jaringan sebagai jaring pengaman.
"""

from __future__ import annotations

import sys
import types

import pytest


@pytest.fixture
def ddgs_palsu(monkeypatch):
    """Pasang ddgs palsu sekali per tes.

    Mengembalikan fungsi pasang(baris). Juga mengganti modul ddgs asli di
    sys.modules supaya 'from ddgs import DDGS' di dalam
    search_with_provenance mengambil tiruan ini, bukan ddgs sungguhan.
    """
    state = {"baris": []}

    class _DDGS:
        def __init__(self, *a, **k):
            pass

        @staticmethod
        def text(query, max_results=5, **kw):
            if state["baris"] is None:
                raise AssertionError(
                    "ddgs palsu tidak dikonfigurasi; pencarian sungguhan "
                    "tidak boleh terjadi di tes ini"
                )
            return list(state["baris"])

    modul = types.ModuleType("ddgs")
    modul.DDGS = _DDGS
    monkeypatch.setitem(sys.modules, "ddgs", modul)

    def pasang(baris):
        state["baris"] = baris

    return pasang


def _cari(engine="brave", n=5):
    import webget  # noqa: F401 - memastikan paket dan modulnya terdaftar
    sm = sys.modules["webget.search"]
    return sm.search_with_provenance("uji", n=n, engine=engine)


class TestBarisCacat:
    def test_baris_tanpa_title_tidak_menggagalkan_semua(self, ddgs_palsu, tmp_path, monkeypatch):
        """Inti temuan: 1 baris tanpa title tidak boleh membuang hasil lain."""
        monkeypatch.setenv("WEBGET_ENGINE_HEALTH", str(tmp_path / "h.json"))
        ddgs_palsu([
            {"title": "Bagus", "href": "https://a.test/1", "body": "isi"},
            {"href": "https://a.test/2", "body": "tanpa title"},
        ])
        hasil, _ = _cari()
        judul = [h["title"] for h in hasil]
        assert "Bagus" in judul, f"baris bagus hilang: {hasil}"
        assert len(hasil) == 2, f"baris tanpa title seharusnya tetap ada: {hasil}"

    def test_baris_tanpa_href_dilewati(self, ddgs_palsu, tmp_path, monkeypatch):
        monkeypatch.setenv("WEBGET_ENGINE_HEALTH", str(tmp_path / "h.json"))
        ddgs_palsu([
            {"title": "Bagus", "href": "https://a.test/1", "body": "isi"},
            {"title": "Tanpa URL", "body": "tidak bisa dibuka"},
        ])
        hasil, _ = _cari()
        assert len(hasil) == 1, f"baris tanpa href seharusnya dilewati: {hasil}"
        assert hasil[0]["url"] == "https://a.test/1"

    def test_semua_baris_cacat_tidak_melempar(self, ddgs_palsu, tmp_path, monkeypatch):
        """Kalau semua baris cacat, hasilnya kosong - bukan KeyError."""
        monkeypatch.setenv("WEBGET_ENGINE_HEALTH", str(tmp_path / "h.json"))
        ddgs_palsu([{"body": "tidak ada title maupun href"}])
        hasil, _ = _cari()
        assert hasil == []

    def test_title_none_jadi_string_kosong(self, ddgs_palsu, tmp_path, monkeypatch):
        monkeypatch.setenv("WEBGET_ENGINE_HEALTH", str(tmp_path / "h.json"))
        ddgs_palsu([{"title": None, "href": "https://a.test/", "body": None}])
        hasil, _ = _cari()
        assert hasil == [{"title": "", "url": "https://a.test/", "snippet": ""}]

    def test_title_kosong_dipertahankan(self, ddgs_palsu, tmp_path, monkeypatch):
        """Judul kosong tapi URL ada: tetap berguna, jangan dibuang."""
        monkeypatch.setenv("WEBGET_ENGINE_HEALTH", str(tmp_path / "h.json"))
        ddgs_palsu([{"title": "", "href": "https://a.test/"}])
        hasil, _ = _cari()
        assert len(hasil) == 1
        assert hasil[0]["url"] == "https://a.test/"


class TestPerilakuNormal:
    def test_baris_lengkap_dinormalisasi(self, ddgs_palsu, tmp_path, monkeypatch):
        monkeypatch.setenv("WEBGET_ENGINE_HEALTH", str(tmp_path / "h.json"))
        ddgs_palsu([{"title": "Judul", "href": "https://a.test/", "body": "ringkasan"}])
        hasil, prov = _cari()
        assert hasil == [
            {"title": "Judul", "url": "https://a.test/", "snippet": "ringkasan"}
        ]
        assert prov["engine"] == "brave"

    def test_body_hilang_jadi_string_kosong(self, ddgs_palsu, tmp_path, monkeypatch):
        monkeypatch.setenv("WEBGET_ENGINE_HEALTH", str(tmp_path / "h.json"))
        ddgs_palsu([{"title": "T", "href": "https://a.test/"}])
        hasil, _ = _cari()
        assert hasil[0]["snippet"] == ""


class TestLedgerTidakTerpengaruh:
    def test_engine_dengan_baris_cacat_dicatat_sukses(self, ddgs_palsu, tmp_path, monkeypatch):
        """Klaim lama gue (engine tercatat gagal) terbukti salah.

        _record dipanggil SEBELUM normalisasi dengan bool(rows). Karena
        rows tidak kosong, hasilnya sukses (ok=1.0). Tes ini mengunci
        perilaku itu supaya klaim keliru tidak diulang.
        """
        from webget import health

        monkeypatch.setenv("WEBGET_ENGINE_HEALTH", str(tmp_path / "h.json"))
        ddgs_palsu([{"href": "https://a.test/", "body": "tanpa title"}])
        _cari()
        entri = health.load().get("brave")
        assert entri is not None, "engine tidak tercatat sama sekali"
        assert entri["ok"] == 1.0, (
            f"engine dicatat {entri['ok']}, bukan sukses; klaim lama gue "
            f"bahwa baris cacat menurunkan skor engine ternyata salah"
        )
