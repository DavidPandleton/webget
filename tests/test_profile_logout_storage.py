"""Tes profile: logout harus menghapus localStorage juga, bukan hanya cookie.

Ditemukan saat mengaudit profile.py. Docstring modul menyatakan profil
menyimpan "(cookies + local storage)". Tapi _prune_storage_cookies hanya
mengosongkan state["cookies"]; state["origins"] (tempat Playwright
menyimpan localStorage/sessionStorage) dibiarkan utuh.

Situs SPA - termasuk banyak portal kampus - sering menyimpan token sesi
di localStorage, bukan di cookie. Setelah "logout", token itu masih ada,
sehingga fetch berikutnya dengan profil yang sama masih terautentikasi.
Pengguna yang percaya sesinya sudah berakhir justru masih login; itu
masalah keamanan, bukan sekadar sisa data.

Dibuktikan sebelum perbaikan: token di localStorage tetap ada setelah
_prune_storage_cookies(state, "kampus.test").
"""

from __future__ import annotations

import copy

from webget.profile import _prune_storage_cookies


def _state_uji():
    return {
        "cookies": [
            {"name": "sess", "value": "rahasia", "domain": ".kampus.test"},
            {"name": "lain", "value": "biarkan", "domain": ".github.com"},
        ],
        "origins": [
            {
                "origin": "https://kampus.test",
                "localStorage": [
                    {"name": "token", "value": "TOKEN-SESI-RAHASIA"},
                    {"name": "user", "value": "hermawan"},
                ],
            },
            {
                "origin": "https://sub.kampus.test",
                "localStorage": [{"name": "token2", "value": "RAHASIA-2"}],
            },
            {
                "origin": "https://github.com",
                "localStorage": [{"name": "gh", "value": "biarkan"}],
            },
        ],
    }


class TestLocalStorageDihapus:
    def test_token_localstorage_host_terhapus(self):
        """Inti temuan: token di localStorage harus ikut terhapus."""
        state, _ = _prune_storage_cookies(copy.deepcopy(_state_uji()), "kampus.test")
        semua = [
            ent.get("name")
            for o in (state.get("origins") or [])
            for ent in (o.get("localStorage") or [])
        ]
        assert "token" not in semua, (
            f"token di localStorage masih ada setelah logout; "
            f"origins={state.get('origins')}"
        )
        assert "token2" not in semua, "localStorage subdomain tidak terhapus"

    def test_origin_host_dihapus_seluruhnya(self):
        state, _ = _prune_storage_cookies(copy.deepcopy(_state_uji()), "kampus.test")
        asal = [o.get("origin") for o in (state.get("origins") or [])]
        assert not any("kampus.test" in (a or "") for a in asal), (
            f"origin kampus.test masih ada: {asal}"
        )

    def test_host_lain_tidak_ikut_terhapus(self):
        """Perbaikan tidak boleh terlalu luas: github.com harus tetap utuh."""
        state, _ = _prune_storage_cookies(copy.deepcopy(_state_uji()), "kampus.test")
        asal = [o.get("origin") for o in (state.get("origins") or [])]
        assert any("github.com" in (a or "") for a in asal), (
            f"github.com ikut terhapus: {asal}"
        )
        sisa_cookie = [c["name"] for c in state.get("cookies", [])]
        assert "lain" in sisa_cookie, "cookie host lain ikut terhapus"


class TestCookieTetapBerfungsi:
    def test_cookie_host_terhapus(self):
        state, _ = _prune_storage_cookies(copy.deepcopy(_state_uji()), "kampus.test")
        sisa = [c["name"] for c in state.get("cookies", [])]
        assert "sess" not in sisa
        assert "lain" in sisa

    def test_removed_menghitung_cookie_dan_storage(self):
        """removed harus mencerminkan seluruh data sesi yang dibuang.

        Sebelumnya hanya menghitung cookie, sehingga laporan ke pengguna
        ("N cookie dihapus") menyembunyikan localStorage yang sebenarnya
        juga dibersihkan.
        """
        _, dihapus = _prune_storage_cookies(copy.deepcopy(_state_uji()), "kampus.test")
        # 1 cookie + 2 localStorage (kampus.test) + 1 localStorage (subdomain)
        assert dihapus == 4, f"removed={dihapus}, seharusnya 4"


class TestTahanInputAneh:
    def test_state_tanpa_origins(self):
        _, dihapus = _prune_storage_cookies({"cookies": []}, "kampus.test")
        assert dihapus == 0

    def test_state_kosong(self):
        _, dihapus = _prune_storage_cookies({}, "kampus.test")
        assert dihapus == 0

    def test_origin_tanpa_localstorage(self):
        state, _ = _prune_storage_cookies(
            {"cookies": [], "origins": [{"origin": "https://kampus.test"}]}, "kampus.test"
        )
        assert state["origins"] == []

    def test_origin_dengan_port(self):
        state, _ = _prune_storage_cookies(
            {"cookies": [], "origins": [{"origin": "https://kampus.test:8443"}]},
            "kampus.test",
        )
        assert state["origins"] == [], "origin dengan port tidak terhapus"
