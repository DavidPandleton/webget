"""Tes untuk surfacing `metadata.statusCode` Firecrawl.

Konteks (backlog webget): `fetch_firecrawl` melaporkan `r.status_code` - status
transport ke api.firecrawl.dev, yang di baris 51 udah dipastikan 200. Jadi
field `status_code` **selalu 200**, apa pun isi halaman target. Dan saat target
menolak (403/404) sehingga markdown kosong, pesan errornya cuma "empty result",
yang nyembunyiin sebab sebenarnya.

Tes ini mengunci perilaku yang benar. Semua pakai mock HTTP: tidak ada jaringan.
"""
import asyncio
from unittest.mock import patch

import pytest

from webget import firecrawl


def _resp(markdown, *, title="Judul", status=None, omit_status=False):
    """Bangun respons Firecrawl tiruan (transport 200 + body)."""

    class _R:
        status_code = 200

        def json(self):
            meta = {"title": title}
            if not omit_status:
                meta["statusCode"] = status
            return {"data": {"markdown": markdown, "metadata": meta}}

    return _R()


async def _call(resp, url="https://contoh.test/halaman"):
    with patch("httpx.AsyncClient") as client:
        inst = client.return_value.__aenter__.return_value

        async def _post(*a, **k):
            return resp

        inst.post = _post
        return await firecrawl.fetch_firecrawl(url, 5000, "kunci", 20)


# --- field status_code ---

def test_status_code_lapor_status_target_bukan_transport():
    """status_code harus status HALAMAN, bukan status transport (selalu 200)."""
    out = asyncio.run(_call(_resp("# Isi", status=403)))
    assert out["status_code"] == 403, (
        "status_code melaporkan status transport, bukan status target"
    )


def test_status_code_200_saat_target_200():
    out = asyncio.run(_call(_resp("# Isi", status=200)))
    assert out["status_code"] == 200


def test_status_code_string_numerik_diterima():
    """Firecrawl kadang kirim angka sebagai string."""
    out = asyncio.run(_call(_resp("# Isi", status="503")))
    assert out["status_code"] == 503


def test_status_code_absen_jatuh_ke_transport():
    """Tanpa metadata.statusCode, perilaku lama dipertahankan (200)."""
    out = asyncio.run(_call(_resp("# Isi", omit_status=True)))
    assert out["status_code"] == 200


def test_status_code_rusak_tidak_ditebak():
    """Nilai tak terbaca -> None -> jatuh ke transport, bukan status karangan."""
    out = asyncio.run(_call(_resp("# Isi", status="bukan-angka")))
    assert out["status_code"] == 200


# --- pesan error saat markdown kosong ---

def test_empty_result_menyebut_status_target():
    """403/404 harus kelihatan di pesan error, bukan cuma 'empty result'."""
    with pytest.raises(RuntimeError) as e:
        asyncio.run(_call(_resp("", status=404)))
    assert "404" in str(e.value)
    assert "empty result" in str(e.value).lower()


def test_empty_result_tanpa_status_tetap_berfungsi():
    """Kalau Firecrawl tidak kasih status, pesan lama tetap dipakai."""
    with pytest.raises(RuntimeError) as e:
        asyncio.run(_call(_resp("", omit_status=True)))
    assert str(e.value) == "Firecrawl empty result"


# --- _coerce_status ---

@pytest.mark.parametrize(
    "nilai,harapan",
    [
        (200, 200),
        ("404", 404),
        (None, None),
        ("", None),
        ("abc", None),
        (0, None),        # di luar rentang HTTP
        (999, None),      # di luar rentang HTTP
        (True, None),     # bool bukan status
        (False, None),
        (599, 599),       # batas atas sah
        (100, 100),       # batas bawah sah
    ],
)
def test_coerce_status(nilai, harapan):
    assert firecrawl._coerce_status(nilai) == harapan
