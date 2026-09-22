"""Tes cache: WEBGET_CACHE_MAX tidak valid tidak boleh menghapus segalanya.

Eviction di cache_put:

    cap = int(os.environ.get("WEBGET_CACHE_MAX", "5000"))
    ...
    if len(files) > cap:
        ... buang yang kedaluwarsa ...
        if len(live) > cap:
            live.sort(key=os.path.getmtime)
            for f in live[: len(live) - int(cap * 0.8)]:
                os.remove(f)

Dengan cap <= 0, irisan itu mencakup SELURUH daftar, sehingga satu
penulisan menghapus seluruh cache:

    cap = 0   -> int(0*0.8) == 0,        live[:len(live)]     -> semua
    cap = -5  -> int(-5*0.8) == -4,      live[:len(live)+4]   -> semua

Sebelumnya hanya ValueError yang ditangani, jadi "abc" aman tapi "0" dan
"-5" justru paling merusak. Sekarang cap <= 0 juga kembali ke bawaan.
"""

from __future__ import annotations

import os

import pytest

from webget import cache


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    """Arahkan cache ke tmp_path SEBELUM penulisan apa pun.

    Hook override ada di SHIM `webget_cli.CACHE_DIR`, bukan
    `webget.cache.CACHE_DIR`: _cache_dir() mengimpor webget_cli dan membaca
    atributnya, sehingga menetapkan atribut di modul cache sendiri tidak
    berpengaruh. Versi pertama tes ini menambal nama yang salah, portanto
    nol file tertulis di tmp dan assertion-nya lolos/gagal karena salah
    alasan.
    """
    import webget_cli

    d = tmp_path / "cache"
    d.mkdir()
    monkeypatch.setattr(webget_cli, "CACHE_DIR", str(d))
    return d


def _isi(d, n):
    for i in range(n):
        cache.cache_put(
            url=f"https://x.test/{i}",
            cookies=None,
            headers=None,
            max_chars=1000,
            data={"markdown": f"isi {i}", "status": "success"},
        )
    return len([f for f in os.listdir(d) if f.endswith(".json")])


class TestCapTidakValid:
    @pytest.mark.parametrize("nilai", ["0", "-5", "-1"])
    def test_cap_nol_atau_negatif_tidak_menghapus_semua(self, cache_dir, monkeypatch, nilai):
        """cap <= 0 harus jatuh ke bawaan, bukan menghapus seluruh cache."""
        monkeypatch.setenv("WEBGET_CACHE_MAX", nilai)
        sebelum = _isi(cache_dir, 10)
        assert sebelum == 10, f"setup gagal: hanya {sebelum} entri tertulis"

        # Satu penulisan tambahan memicu jalur eviction.
        cache.cache_put(
            url="https://x.test/baru",
            cookies=None,
            headers=None,
            max_chars=1000,
            data={"markdown": "baru", "status": "success"},
        )
        sesudah = len([f for f in os.listdir(cache_dir) if f.endswith(".json")])
        assert sesudah == 11, (
            f"WEBGET_CACHE_MAX={nilai} menghapus cache: {sebelum} -> {sesudah}. "
            f"cap <= 0 membuat irisan eviction mencakup semua entri."
        )

    def test_cap_bukan_angka_jatuh_ke_bawaan(self, cache_dir, monkeypatch):
        monkeypatch.setenv("WEBGET_CACHE_MAX", "abc")
        sebelum = _isi(cache_dir, 5)
        cache.cache_put(
            url="https://x.test/baru", cookies=None, headers=None,
            max_chars=1000, data={"markdown": "x", "status": "success"},
        )
        sesudah = len([f for f in os.listdir(cache_dir) if f.endswith(".json")])
        assert sesudah >= sebelum, "nilai tidak valid tidak boleh menghapus apa pun"


class TestEvictionNormal:
    def test_cap_kecil_membatasi_jumlah_entri(self, cache_dir, monkeypatch):
        """cap yang sah tetap mengecilkan cache, tapi tidak ke nol.

        Ini bukan bug: cap=2 menyisakan int(2*0.8)==1 entri, jadi cache
        memang agresif untuk cap sangat kecil. Yang diuji adalah bahwa
        eviction benar-benar jalan dan menyisakan sesuatu, bukan menghapus
        semuanya.
        """
        monkeypatch.setenv("WEBGET_CACHE_MAX", "5")
        _isi(cache_dir, 12)
        cache.cache_put(
            url="https://x.test/picu", cookies=None, headers=None,
            max_chars=1000, data={"markdown": "x", "status": "success"},
        )
        sisa = len([f for f in os.listdir(cache_dir) if f.endswith(".json")])
        assert 0 < sisa <= 12, f"eviction menyisakan {sisa} entri"
        # int(5*0.8) == 4, jadi harusnya menyisakan sekitar 4.
        assert sisa <= 6, f"cap=5 seharusnya memangkas ke sekitar 4, dapat {sisa}"
