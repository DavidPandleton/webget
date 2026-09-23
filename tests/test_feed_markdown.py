"""Tes: teks feed pihak ketiga tidak boleh merusak struktur markdown.

_feed_to_links menyusun output dari judul/deskripsi feed yang tidak kita
kendalikan:

    lines.append(f"- [{title}]({link})")
    lines.append(f"  > {desc[:200]}")

Kalau title atau desc memuat newline, strukturnya rusak:

    - [Judul dengan
    baris baru](https://x.test/b)     <- link kehilangan penutup
      > Baris pertama deskripsi.
    Baris kedua ...                   <- keluar dari blok kutipan

Selain itu [:200] memotong di tengah kata. Tes di bawah mengunci bahwa
setiap item jadi tepat satu baris yang rapi.
"""

from __future__ import annotations

from webget.http import _convert_non_html, _feed_to_links

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>Judul dengan
baris baru</title><link>https://x.test/b</link>
<description>Baris pertama deskripsi.
Baris kedua di baris terpisah.</description></item>
</channel></rss>"""

ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Atom dengan
newline</title><link href="https://x.test/a"/>
<summary>Ringkasan pertama.
Ringkasan kedua.</summary></entry>
</feed>"""


def _baris_markdown(teks):
    return [ln for ln in teks.splitlines() if ln.strip()]


class TestJudulDanDeskripsiSatuBaris:
    def test_rss_newline_tidak_memecah_struktur(self):
        hasil = _feed_to_links(RSS.encode())
        baris = _baris_markdown(hasil)
        # Hanya boleh ada 2 baris: satu item, satu deskripsi.
        assert len(baris) == 2, f"struktur rusak:\n{hasil}"
        assert baris[0].startswith("- [") and baris[0].endswith(")"), (
            f"link judul tidak utuh: {baris[0]!r}"
        )
        assert baris[1].startswith("  > "), f"deskripsi bukan blok kutipan: {baris[1]!r}"

    def test_atom_newline_tidak_memecah_struktur(self):
        hasil = _feed_to_links(ATOM.encode())
        baris = _baris_markdown(hasil)
        assert len(baris) == 2, f"struktur rusak:\n{hasil}"
        assert baris[0].startswith("- [") and baris[0].endswith(")")

    def test_tidak_ada_baris_keluar_dari_struktur(self):
        """Setiap baris harus salah satu dari: item atau deskripsi."""
        hasil = _feed_to_links(RSS.encode())
        for ln in _baris_markdown(hasil):
            assert ln.startswith(("- ", "  > ")), (
                f"baris tak terduga di output: {ln!r}"
            )


class TestPemotonganDeskripsi:
    def test_potong_di_batas_kata(self):
        panjang = "kata " * 100
        xml = (
            '<?xml version="1.0"?><rss><channel><item>'
            "<title>P</title><link>https://x.test/c</link>"
            f"<description>{panjang}</description>"
            "</item></channel></rss>"
        )
        hasil = _feed_to_links(xml.encode())
        deskripsi = _baris_markdown(hasil)[1]
        assert deskripsi.endswith("..."), f"tidak ada penanda potong: {deskripsi!r}"
        # Tidak boleh memutus kata: sebelum " ..." harus spasi, bukan huruf.
        inti = deskripsi.rstrip(".").rstrip()
        assert inti.endswith("kata"), f"kata terpotong: {deskripsi[-30:]!r}"

    def test_deskripsi_pendek_tidak_dipotong(self):
        pendek = "Ringkas saja."
        xml = (
            '<?xml version="1.0"?><rss><channel><item>'
            f"<title>T</title><link>https://x.test/d</link>"
            f"<description>{pendek}</description></item></channel></rss>"
        )
        hasil = _feed_to_links(xml.encode())
        assert "Ringkas saja." in hasil
        assert "..." not in hasil


class TestLewatConvert:
    def test_convert_non_html_rapi(self):
        """Jalur lengkap _convert_non_html memakai perbaikan yang sama."""
        markdown = _convert_non_html(
            "application/rss+xml", RSS.encode(), "https://x.test/feed"
        )[1]
        for ln in _baris_markdown(markdown):
            assert ln.startswith(("- ", "  > ")), (
                f"baris tak terduga: {ln!r}"
            )
