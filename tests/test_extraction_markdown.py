"""Extraction tests: trafilatura first, markdownify fallback (0.7.3).

0.7.3 swapped the HTML-to-Markdown fallback from html2text (GPL-3.0) to
markdownify (MIT) for license hygiene. These tests pin the SEMANTIC
behavior of the fallback: headings, paragraphs, links, lists, code,
malformed input, and the trafilatura-fail / too-thin fallback triggers.
Output does not need to be byte-identical to html2text, but links and
structure must be preserved.
"""

import webget_cli as webget


def _extract(html):
    return webget._extract_markdown(html)


def _force_fallback(monkeypatch, result=None):
    """Make trafilatura fail (or return a too-thin result) so the
    markdownify fallback path runs."""
    import trafilatura

    monkeypatch.setattr(trafilatura, "extract", lambda *a, **k: result)


class TestMarkdownifyFallback:
    BODY = "<p>" + "x" * 60 + "</p>"  # keep fixture output above the 50-char threshold

    def test_heading(self, monkeypatch):
        _force_fallback(monkeypatch)
        out = _extract("<h1>Big Title</h1>" + self.BODY)
        assert "# Big Title" in out

    def test_paragraph_and_bold(self, monkeypatch):
        _force_fallback(monkeypatch)
        out = _extract("<p>Hello <b>world</b>!</p>" + self.BODY)
        assert "Hello **world**!" in out

    def test_link_preserved(self, monkeypatch):
        # THE critical semantic: links must survive (html2text -> markdownify).
        _force_fallback(monkeypatch)
        out = _extract('<p><a href="https://example.com">Example</a></p>' + self.BODY)
        assert "[Example](https://example.com)" in out

    def test_list_items_preserved(self, monkeypatch):
        _force_fallback(monkeypatch)
        out = _extract("<ul><li>alpha</li><li>beta</li></ul>" + self.BODY)
        assert "alpha" in out and "beta" in out
        assert "- alpha" in out or "* alpha" in out  # bullet marker present

    def test_code_block_preserved(self, monkeypatch):
        _force_fallback(monkeypatch)
        out = _extract("<pre><code>def f():\n    return 1</code></pre>" + self.BODY)
        assert "def f():" in out and "return 1" in out

    def test_table_cells_preserved(self, monkeypatch):
        _force_fallback(monkeypatch)
        out = _extract("<table><tr><td>cell1</td><td>cell2</td></tr></table>" + self.BODY)
        assert "cell1" in out and "cell2" in out

    def test_malformed_html_no_crash(self, monkeypatch):
        _force_fallback(monkeypatch)
        out = _extract("<div><p>unclosed <b>bold</div>")
        assert isinstance(out, str)  # never raises, never non-str

    def test_empty_html_returns_empty(self, monkeypatch):
        _force_fallback(monkeypatch)
        assert _extract("") == ""
        assert _extract("<html><head><title>x</title></head><body></body></html>") == ""

    def test_trafilatura_failure_falls_back(self, monkeypatch):
        # trafilatura raises -> markdownify must still produce output
        import trafilatura

        def boom(*a, **k):
            raise RuntimeError("extraction backend down")

        monkeypatch.setattr(trafilatura, "extract", boom)
        html = '<p>Some <a href="https://example.com">linked</a> content here.</p>' + self.BODY
        out = _extract(html)
        assert out != ""
        assert "linked" in out

    def test_thin_trafilatura_result_falls_back(self, monkeypatch):
        # trafilatura returns <100 chars -> markdownify fallback runs
        _force_fallback(monkeypatch, result="too short")
        html = '<p>Long enough content <a href="https://example.com">with link</a></p>' + self.BODY
        out = _extract(html)
        assert "with link" in out
        assert out != "too short"


class TestTrafilaturaPrimary:
    def test_trafilatura_wins_when_rich(self):
        body = "<p>" + "The quick brown fox jumps over the lazy dog. " * 8 + "</p>"
        out = _extract(f"<html><body><h1>Doc</h1>{body}</body></html>")
        assert "quick brown fox" in out
