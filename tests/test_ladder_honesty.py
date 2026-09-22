"""Tes untuk pelaporan alasan yang jujur di ladder.

Ditemukan saat mengaudit klaim integritas webget. README webget
menjanjikan "never pretends an empty page is success" dan "it tells you
honestly what happened". Sebuah langkah ladder yang menolak konten
karena terlalu tipis DICATAT sebagai state "success" - jadi `reasons`
memuat success untuk langkah yang gagal, dan itu bertentangan dengan
janji tersebut.

Kenapa berbahaya: pemanggil program (agent) membaca `reasons` untuk
memutuskan langkah berikutnya. Melihat "success" untuk langkah yang
sebenarnya ditolak membuat keputusan itu salah.
"""

from __future__ import annotations

import asyncio

import pytest

from webget import ladder


def _ambil(satu_hasil):
    """Ambil objek hasil dari keluaran {"<url>": {...}}."""
    if isinstance(satu_hasil, dict) and "status" in satu_hasil:
        return satu_hasil
    for nilai in satu_hasil.values():
        if isinstance(nilai, dict) and "status" in nilai:
            return nilai
    raise AssertionError(f"tidak ada objek hasil di {satu_hasil!r}")


class TestThinContentReason:
    """Langkah yang menolak konten tipis tidak boleh melaporkan success."""

    def test_thin_http_step_is_not_reported_as_success(self, fresh_cache):
        """Inti temuan: alasan untuk langkah tipis tidak boleh 'success'.

        Server /empty membalas 200 dengan badan kosong. Jalur http
        mengambilnya, tapi kontennya ditolak karena tipis. Sebelum
        perbaikan, reasons memuat ("success", "http", "content too thin").
        """
        server = fresh_cache
        hasil = asyncio.run(ladder.scrape_many([server.url("/empty")], strategy="http"))
        out = _ambil(hasil)

        # Status akhir "thin" (dulu "error" generik). Lebih spesifik:
        # membedakan "permintaan berhasil tapi konten ditolak" dari
        # "koneksi gagal", dan keduanya butuh tindakan yang berbeda.
        assert out["status"] == "thin"

        reasons = out.get("reasons") or []
        assert reasons, "reasons harus ada supaya pemanggil tahu sebabnya"

        # Tidak boleh ada satu pun alasan berstate "success". Setiap entri
        # yang berstate success berarti langkah itu BERHASIL, dan kalau
        # berhasil, hasil akhirnya tidak akan error.
        for state, method, detail in reasons:
            assert state != "success", (
                f"langkah {method} melaporkan state=success padahal hasil "
                f"akhir error dengan detail={detail!r}"
            )

    def test_thin_content_error_names_the_method(self, fresh_cache):
        """Pesan error akhir menyebut metode, supaya bisa ditindaklanjuti.

        Dulu detail digabung "; " tanpa metode, jadi "content too thin"
        tidak memberi tahu langkah mana yang menolak. Pemanggil tidak
        bisa tahu apakah harus memasang crawl4ai atau menaikkan max_chars.
        """
        server = fresh_cache
        hasil = asyncio.run(ladder.scrape_many([server.url("/empty")], strategy="http"))
        out = _ambil(hasil)
        pesan = out.get("error") or ""
        assert "http" in pesan, (
            f"pesan error {pesan!r} tidak menyebut metode yang menolak"
        )

    def test_success_carries_empty_reasons(self, fresh_cache):
        """Sebaliknya: langkah yang BENAR-BENAR berhasil punya reasons
        kosong, sehingga 'reasons berisi success' hanya bisa muncul dari
        kegagalan."""
        server = fresh_cache
        hasil = asyncio.run(ladder.scrape_many([server.url("/json")], strategy="http"))
        out = _ambil(hasil)
        assert out["status"] == "success"
        assert out["reasons"] == []


class TestTerminalState:
    """_terminal_state tidak boleh memilih 'success' dari reasons."""

    def test_success_state_in_reasons_is_ignored(self):
        """Kalau ada entri berstate success, ia tidak menang.

        Ini alasan bug di atas tidak terlihat: 'success' tidak ada di
        daftar prioritas, jadi entri itu diabaikan dan hasil akhir jadi
        error generik. Akibatnya reasons dan status akhir saling
        bertentangan tanpa ada yang menyadarinya.
        """
        state, _, _, _ = ladder._terminal_state(
            [("success", "http", "content too thin")], None
        )
        assert state != "success"

    def test_combined_detail_names_each_method(self):
        """Alasan gabungan menyebut metode tiap langkah."""
        _, _, detail, _ = ladder._terminal_state(
            [
                ("error", "http", "konten terlalu tipis"),
                ("error", "crawl4ai", "tidak terpasang"),
            ],
            None,
        )
        assert "http" in detail
        assert "crawl4ai" in detail

    def test_thin_beats_generic_error_in_priority(self):
        """Penolakan tipis lebih informatif daripada kegagalan koneksi.

        Kalau satu langkah gagal koneksi dan langkah lain menolak konten
        tipis, yang dilaporkan adalah penolakan tipis: itu memberi tahu
        pemanggil bahwa permintaannya SEMPAT sampai ke server, dan yang
        perlu diubah adalah ambang atau strategi - bukan jaringan.
        """
        state, _, detail, method = ladder._terminal_state(
            [
                ("error", "http", "koneksi ditolak"),
                ("thin", "crawl4ai", "content too thin"),
            ],
            None,
        )
        assert state == "thin"
        assert method == "crawl4ai"
        assert "content too thin" in detail

    def test_priority_still_prefers_challenge(self):
        """Perbaikan tidak mengubah prioritas yang sudah ada."""
        state, _, _, method = ladder._terminal_state(
            [
                ("error", "http", "tipis"),
                ("challenge", "crawl4ai", "perlu verifikasi"),
            ],
            None,
        )
        assert state == "challenge"
        assert method == "crawl4ai"

    def test_login_required_still_sets_authenticated_false(self):
        state, authenticated, _, _ = ladder._terminal_state(
            [("login_required", "http", "sesi kedaluwarsa")], None
        )
        assert state == "login_required"
        assert authenticated is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
