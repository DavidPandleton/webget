"""Tes cli: hasil parse (remaining) HARUS dipakai, bukan args mentah.

Ditemukan saat mengaudit cli.py. parse_opts menghitung `remaining` - daftar
argumen TANPA opsi - dengan benar, tetapi main() membuangnya (RUF059:
tidak pernah dipakai) dan memakai `args` MENTAH untuk semua penempatan
perintah:

    cmd = args[0]
    q = args[1] if len(args) > 1 else ""
    n = limit or (int(args[2]) if len(args) > 2 else 5)

Akibatnya, setiap opsi yang muncul SEBELUM perintah atau query terbaca
sebagai perintah/query. Yang paling parah:

    webget s --json uji-kata

args[2] menjadi "uji-kata", lalu int("uji-kata") melempar ValueError dan
seluruh perintah gagal. Sebelum perbaikan:

    $ webget s --json uji-kata
    ValueError: invalid literal for int() with base 10: 'uji-kata'

Sesudah perbaikan, `remaining` dipakai, sehingga perintah dan query
terbaca apa adanya dan opsi tidak lagi bocor menjadi nilai int().
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stderr, redirect_stdout

import pytest

from webget import cli


def _parse(args):
    """parse_opts mengembalikan tuple panjang; remaining adalah elemen [0]."""
    return cli.parse_opts(args)[0]


class TestRemainingBersih:
    def test_opsi_dibuang_dari_remaining(self):
        assert _parse(["--json", "u", "https://x.test/", "-n", "500"]) == [
            "u",
            "https://x.test/",
        ]

    def test_urutan_argumen_dipertahankan(self):
        assert _parse(["s", "kata", "10", "--limit", "3"]) == ["s", "kata", "10"]

    def test_hanya_opsi(self):
        assert _parse(["--json", "--no-cache"]) == []


class TestMainPakaiRemaining:
    def test_opsi_sebelum_perintah_tidak_bikin_gagal(self, monkeypatch, tmp_path):
        """`--json u URL` harus mengenali perintah 'u', bukan '--json'.

        Diuji pada tingkat parse_opts + penempatan perintah, tanpa jaringan.
        """
        remaining = _parse(["--json", "u", "https://example.com/"])
        cmd = remaining[0]
        if cmd == "fetch":
            cmd = "u"
        assert cmd == "u", f"perintah terbaca {cmd!r}, bukan 'u'"

    def test_opsi_sebelum_query_tidak_mengubah_query(self):
        """`s --json uji-kata`: query tetap 'uji-kata', bukan '--json'."""
        remaining = _parse(["s", "--json", "uji-kata"])
        assert remaining == ["s", "uji-kata"]
        assert remaining[1] == "uji-kata"

    def test_argumen_ketiga_bukan_opsi(self):
        """`s --json uji-kata`: remaining[2] tidak ada, jadi int() dilewati.

        Sebelum perbaikan, args[2] = 'uji-kata' dan int() melempar
        ValueError, menggagalkan perintah.
        """
        remaining = _parse(["s", "--json", "uji-kata"])
        assert len(remaining) <= 2, (
            f"remaining={remaining}; token opsi bocor menjadi argumen ketiga"
        )

    def test_jumlah_hasil_dari_argumen_ketiga(self):
        remaining = _parse(["s", "kata", "7", "--json"])
        assert remaining[2] == "7"
        assert int(remaining[2]) == 7


class TestJumlahHasilTidakValid:
    """Argumen posisional (jumlah hasil) juga harus ditolak dengan bersih.

    Argumen ketiga tidak melewati parse_opts, sehingga `webget s kata abc`
    sempat membuang traceback:

        File ".../webget/cli.py", line 523, in main
          n = limit or (int(remaining[2]) if len(remaining) > 2 else 5)
        ValueError: invalid literal for int() with base 10: 'abc'

    Berlaku untuk 's' (bawaan 5) dan 'su' (bawaan 3).
    """

    @pytest.mark.parametrize(
        "args",
        [
            ["s", "kata", "abc"],
            ["su", "kata", "abc"],
            ["s", "kata", "-5"],
            ["su", "kata", "0"],
        ],
    )
    def test_pesan_bersih_bukan_traceback(self, monkeypatch, args):
        monkeypatch.setattr(sys, "argv", ["webget"] + args)
        buf = io.StringIO()
        with pytest.raises(SystemExit) as keluar, redirect_stdout(buf), redirect_stderr(buf):
            cli.main()
        keluaran = buf.getvalue()
        assert keluar.value.code == 2
        assert "error:" in keluaran, f"tidak ada pesan error:\n{keluaran}"
        assert "result count" in keluaran
        assert "Traceback" not in keluaran, f"pengguna melihat traceback:\n{keluaran}"

    def test_limit_menang_atas_argumen_posisional(self, monkeypatch):
        """--limit (sudah divalidasi) tidak boleh dikalahkan argumen buruk.

        Karena --limit menang, argumen ketiga yang buruk tidak pernah
        dievaluasi, jadi tidak boleh menggagalkan perintah.
        """
        remaining = _parse(["s", "kata", "abc", "--limit", "4"])
        # parse_opts menaruh 'abc' di remaining; --limit terpisah.
        assert remaining == ["s", "kata", "abc"]
        assert cli.parse_opts(["s", "kata", "abc", "--limit", "4"])[8] == 4


class TestBatasAtas:
    """Batas atas disamakan dengan MCP supaya kontraknya satu.

    Sebelumnya MCP membatasi max_chars <= 1_000_000 dan n <= 50, sementara
    CLI tidak punya batas atas sama sekali. Input yang sama diterima di CLI
    dan ditolak di MCP, sehingga agent yang belajar salah satu antarmuka
    terkejut di antarmuka lain.
    """

    def test_max_chars_dibatasi(self):
        assert cli.parse_opts(["-n", "1000000"])[3] == 1000000
        with pytest.raises(ValueError, match="--max-chars must be <= 1000000"):
            cli.parse_opts(["-n", "1000001"])

    @pytest.mark.parametrize("n", [51, 1000000])
    def test_jumlah_hasil_dibatasi(self, monkeypatch, n):
        """Batas 50 sama dengan _MAX_SEARCH_N di MCP."""
        monkeypatch.setattr(sys, "argv", ["webget", "s", "kata", str(n)])
        buf = io.StringIO()
        with pytest.raises(SystemExit) as keluar, redirect_stdout(buf), redirect_stderr(buf):
            cli.main()
        assert keluar.value.code == 2
        assert "result count must be <= 50" in buf.getvalue()

    def test_jumlah_hasil_50_diterima(self, monkeypatch):
        """Tepi atas tepat di 50 harus diterima, bukan ditolak.

        Dipanggil lewat main() dengan search di-stub, supaya validasi asli
        yang diuji dan tidak ada jaringan yang tersentuh.
        """
        dipanggil = {}

        def _cari_palsu(q, n=5, engine=None):
            dipanggil["n"] = n
            return [], {"engine": "brave", "requested": "brave", "failed_over": False}

        monkeypatch.setattr(cli, "_search_with_prov", _cari_palsu)
        monkeypatch.setattr(sys, "argv", ["webget", "s", "kata", "50"])
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            cli.main()
        assert dipanggil.get("n") == 50, f"n yang dipakai {dipanggil.get('n')}"

    def test_map_pakai_limit(self):
        """--limit untuk map adalah batas JUMLAH URL, bukan hasil cari.

        Saat membersihkan sisa percobaan yang gagal, gue sempat menulis
        `n = 100` di jalur map dan menghapus dukungan --limit. Tes ini
        mengunci bahwa --limit tetap diteruskan ke map.
        """
        import inspect

        src = inspect.getsource(sys.modules["webget.cli"])
        assert "n = limit or 100" in src, "map tidak lagi memakai --limit"


class TestAngkaTidakValidDitolak:
    """parse_opts menolak opsi angka yang salah, dengan pesan menyebut flag."""

    @pytest.mark.parametrize(
        "args,flag",
        [
            (["-n", "abc"], "--max-chars"),
            (["-t", "abc"], "--timeout"),
            (["--limit", "abc"], "--limit"),
            (["--ttl", "abc"], "--ttl"),
            (["--concurrency", "abc"], "--concurrency"),
        ],
    )
    def test_bukan_angka(self, args, flag):
        with pytest.raises(ValueError, match=flag):
            cli.parse_opts(args)

    @pytest.mark.parametrize(
        "args,flag",
        [
            (["-n", "-5"], "--max-chars"),
            (["-n", "0"], "--max-chars"),
            (["-t", "0"], "--timeout"),
            (["-t", "-1"], "--timeout"),
            (["--limit", "-1"], "--limit"),
            (["--limit", "0"], "--limit"),
            (["--concurrency", "0"], "--concurrency"),
        ],
    )
    def test_nilai_mustahil(self, args, flag):
        """Nilai yang secara sintaksis sah tapi tidak bisa bermakna.

        max_chars=-5 dulu diterima dan mengalir ke smart_truncate, yang
        memperlakukan batas negatif sebagai "tanpa batas": 100 karakter
        kembali sebagai 111.
        """
        with pytest.raises(ValueError, match=flag):
            cli.parse_opts(args)

    def test_ttl_nol_diterima(self):
        """--ttl 0 sah: artinya entri selalu kedaluwarsa."""
        assert cli.parse_opts(["--ttl", "0"])[6] == 0


class TestPesanErrorBukanTraceback:
    def test_valueerror_ditangkap_dan_jadi_pesan_bersih(self, monkeypatch):
        """main() harus mencetak 'error: ...' dan keluar 2, bukan traceback.

        Sebelum perbaikan, pemanggilan parse_opts berada di luar try mana pun:

            Traceback (most recent call last):
              File ".../webget/cli.py", line 412, in main
                ) = parse_opts(args)
            ValueError: --max-chars expects a whole number, got 'abc'
        """
        monkeypatch.setattr(sys, "argv", ["webget", "u", "https://x.test/", "-n", "abc"])
        buf = io.StringIO()
        with pytest.raises(SystemExit) as keluar, redirect_stdout(buf), redirect_stderr(buf):
            cli.main()
        assert keluar.value.code == 2
        assert "error:" in buf.getvalue()
        assert "--max-chars" in buf.getvalue()

    def test_pesan_bukan_traceback(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["webget", "u", "https://x.test/", "-n", "abc"])
        buf = io.StringIO()
        with pytest.raises(SystemExit), redirect_stdout(buf), redirect_stderr(buf):
            cli.main()
        keluaran = buf.getvalue()
        assert "Traceback" not in keluaran, f"pengguna melihat traceback:\n{keluaran}"
        assert "error: --max-chars" in keluaran
