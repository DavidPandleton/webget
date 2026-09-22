"""Tes profile._auth_state: deteksi login.

Dua hal diuji di sini:

1. PERBAIKAN yang gue commit: `login_words` memakai kata utuh, bukan
   substring mentah. Sebelumnya "sign in" cocok dengan "design in", dan
   setiap kata yang mengandung "login" dihitung. Sekarang regex kata utuh.

2. FALSE POSITIVE YANG SENGAJA DIBIARKAN. Halaman publik yang punya menu
   "Login" di navigasi DAN input password milik form lain tetap dilaporkan
   "login_required". Tes terakhir mengunci perilaku itu supaya tidak
   mengejutkan, dan docstring-nya menjelaskan mengapa.

Gue sempat mencoba memperbaikinya dengan menuntut perintah login dan input
password berada di <form> yang sama. Itu SALAH: halaman login sungguhan
sering menaruh kata "Login" di <h1> di luar <form>, sehingga kontrol
(portal yang benar-benar butuh login) ikut lolos jadi "success".

Menandai halaman publik sebagai butuh login = pengguna mengejar sesi yang
tidak ada. Melewatkan halaman login sungguhan = pengguna TIDAK diberi tahu
sesinya mati, dan mengambil halaman login sebagai konten yang sah. Yang
kedua lebih berbahaya, jadi bias sengaja diarahkan ke false positive dan
bukan false negative.
"""

from __future__ import annotations

import pytest

from webget.profile import _auth_state


def _hasil(markdown, html, status=200):
    return {"markdown": markdown, "html": html, "status_code": status}


ISI = "Konten yang cukup panjang untuk dianggap isi sungguhan. " * 10


class TestPerbaikanKataUtuh:
    def test_design_in_bukan_sign_in(self):
        """'design in' tidak boleh dihitung sebagai perintah login."""
        state, _ = _auth_state(
            _hasil(f"Kami design in-house. {ISI}", "<p>Tentang kami</p>"), None
        )
        assert state == "success"

    def test_sign_in_utuh_tetap_terdeteksi(self):
        """Perbaikan tidak boleh menghilangkan deteksi yang sah.

        Frasa "sign in to continue" termasuk penanda keharusan, jadi
        terdeteksi lewat jalur login_phrases meskipun tanpa form password.
        """
        state, _ = _auth_state(
            _hasil(f"Please sign in to continue. {ISI}", "<p>a</p>"), None
        )
        assert state == "login_required"


class TestHalamanLoginTanpaForm:
    """Celah yang diperbaiki: SPA tidak mengirim <input type=password>.

    Halaman login yang di-render JavaScript tidak memuat tag form di HTML
    mentah, sehingga pemeriksaan `has_password_input` meleset dan halaman
    itu lolos sebagai "success". Pengguna tidak diberi tahu sesinya mati,
    dan halaman login diambil sebagai konten sah.

    Penanda frasa keharusan menutup celah ini tanpa menandai halaman
    publik yang sekadar menyebut kata "login".
    """

    @pytest.mark.parametrize(
        "teks",
        [
            "Please sign in to continue.",
            "You must login first.",
            "You must log in to view this page.",
            "Login to continue.",
            "Your session has timed out.",
            "Session expired.",
        ],
    )
    def test_frasa_keharusan_terdeteksi(self, teks):
        state, auth = _auth_state(_hasil(f"{teks} {ISI}", "<p>JS app</p>"), None)
        assert state == "login_required", f"{teks!r} tidak terdeteksi"
        assert auth is False

    def test_artikel_tentang_login_tidak_terdeteksi(self):
        """Kata 'login' di artikel/navigasi bukan permintaan otentikasi."""
        state, _ = _auth_state(
            _hasil(f"Artikel ini membahas cara login ke sistem. {ISI}",
                   "<nav>Login</nav>"),
            None,
        )
        assert state == "success"


class TestDeteksiLoginTetapKuat:
    def test_form_login_sungguhan(self):
        state, auth = _auth_state(
            _hasil(
                f"Silakan Login untuk melanjutkan. {ISI}",
                '<form><input type="text" name="u">'
                '<input type="password" name="p"></form>',
            ),
            None,
        )
        assert state == "login_required"
        assert auth is False

    def test_login_di_luar_form_tetap_terdeteksi(self):
        """Kontrol penting: kata Login di <h1>, input password di <form>.

        Inilah kasus yang patah saat gue menuntut keduanya satu <form>.
        Portal kampus sering berbentuk begini, jadi harus tetap terdeteksi.
        """
        state, _ = _auth_state(
            _hasil(
                f"<h1>Login</h1> {ISI}",
                '<form><input type="password" name="p"></form>',
            ),
            None,
        )
        assert state == "login_required"

    def test_status_401(self):
        state, auth = _auth_state(_hasil(ISI, "<p>a</p>", status=401), None)
        assert state == "login_required"
        assert auth is False

    def test_label_kredensial_nim(self):
        """SION memakai label NIM/username tanpa type=password."""
        state, _ = _auth_state(
            _hasil(
                f"Masukkan password dan NIM Anda. {ISI}",
                "<form><input name='nim'></form>",
            ),
            None,
        )
        assert state == "login_required"


class TestFalsePositiveYangDibiarkan:
    def test_menu_login_plus_input_password_lain(self):
        """False positive yang sengaja dibiarkan, dikunci di sini.

        Halaman publik dengan menu "Login" di navigasi DAN input password
        milik form lain (demo/pencarian) dilaporkan butuh login. Ini tidak
        ideal, tapi memperbaikinya tanpa kehilangan kasus
        test_login_di_luar_form_tetap_terdeteksi belum bisa dilakukan
        dengan sinyal murni teks. Lebih baik terkunci dan terlihat daripada
        menjadi kejutan.
        """
        state, _ = _auth_state(
            _hasil(
                f"Selamat datang di dokumentasi publik. {ISI}",
                '<nav><a href="/login">Login</a></nav>'
                '<form action="/cari"><input type="text" name="q">'
                '<input type="password" name="demo"></form>',
            ),
            None,
        )
        assert state == "login_required", (
            "perilaku berubah; kalau false positive ini akhirnya diperbaiki "
            "dengan cara yang aman, ubah tes ini dan hapus docstring-nya"
        )


class TestHalamanPublikNormal:
    def test_input_password_tanpa_kata_login(self):
        state, _ = _auth_state(
            _hasil(f"Alat ini butuh kata sandi untuk demo. {ISI}",
                   '<input type="password" name="demo">'),
            None,
        )
        assert state == "success"

    def test_halaman_bersih_dengan_profil(self):
        """Dengan profil, halaman sukses berarti authenticated=True."""
        state, auth = _auth_state(_hasil(ISI, "<p>a</p>"), "kampus")
        assert state == "success"
        assert auth is True

    def test_halaman_bersih_tanpa_profil(self):
        state, auth = _auth_state(_hasil(ISI, "<p>a</p>"), None)
        assert state == "success"
        assert auth is None
