"""Ide #3: oversized base64 image payloads are stripped from extracted markdown.

Bug scope (probed 2026-09-16, see .hermes/plans/2026-09-16_webget-ide03-base64-strip.md):
the markdownify fallback keeps inline `data:image/...;base64,...` URLs verbatim,
so a single hero image can carry hundreds of KB into the output. The trafilatura
path drops images entirely, so only the fallback needs the strip.
"""

import webget_cli as webget

BIG_PAYLOAD = "iVBORw0KGgo" + "A" * 800  # > _BASE64_PAYLOAD_MIN
SMALL_PAYLOAD = "B" * 120  # <= threshold, must survive


def _page(payload):
    return (
        "<html><head><title>T</title></head><body><article><h1>T</h1>"
        f'<p><img alt="hero shot" src="data:image/png;base64,{payload}"></p>'
        "<p>" + ("Body text filler words here to satisfy the length floor. " * 6) + "</p>"
        "</article></body></html>"
    )


def _fallback_only(monkeypatch):
    """Force the markdownify path (trafilatura returns None)."""
    import trafilatura

    monkeypatch.setattr(trafilatura, "extract", lambda *a, **k: None)


class TestBase64Strip:
    def test_big_payload_stripped_alt_and_mime_survive(self, monkeypatch):
        _fallback_only(monkeypatch)
        text, _ = webget._extract_with_metadata(_page(BIG_PAYLOAD))
        assert BIG_PAYLOAD not in text
        assert "data:image/png;base64,stripped" in text
        assert "hero shot" in text

    def test_small_payload_preserved(self, monkeypatch):
        _fallback_only(monkeypatch)
        text, _ = webget._extract_with_metadata(_page(SMALL_PAYLOAD))
        assert SMALL_PAYLOAD in text

    def test_threshold_boundary(self, monkeypatch):
        _fallback_only(monkeypatch)
        at_limit = "C" * 200
        just_over = "D" * 201
        assert at_limit in webget._extract_with_metadata(_page(at_limit))[0]
        assert just_over not in webget._extract_with_metadata(_page(just_over))[0]

    def test_extract_markdown_contract_unchanged(self, monkeypatch):
        _fallback_only(monkeypatch)
        out = webget._extract_markdown(_page(BIG_PAYLOAD))
        assert isinstance(out, str)
        assert BIG_PAYLOAD not in out
