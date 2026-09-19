import asyncio

import webget_cli as webget
from webget.truncate import smart_truncate

MARKER = "\n\n[...truncated]"


def test_short_text_passthrough():
    assert smart_truncate("Short text.", 100) == "Short text."


def test_exact_length_passthrough():
    assert smart_truncate("abcde", 5) == "abcde"


def test_paragraph_boundary_preferred():
    text = "para one here\n\npara two here"
    assert smart_truncate(text, 20) == "para one here" + MARKER


def test_last_paragraph_boundary_wins():
    text = "alpha\n\nbeta\n\ngamma"
    assert smart_truncate(text, 15) == "alpha\n\nbeta" + MARKER


def test_paragraph_boundary_beats_sentence():
    # The paragraph break sits further along than the last sentence end in
    # the first paragraph; the paragraph boundary must win.
    text = "Alpha. Beta.\n\nGamma delta epsilon."
    assert smart_truncate(text, 20) == "Alpha. Beta." + MARKER


def test_sentence_fallback_when_no_paragraph():
    text = "First sentence. Second sentence. Third sentence."
    assert smart_truncate(text, 25) == "First sentence." + MARKER


def test_sentence_fallback_exclamation_and_question():
    text = "Wow! Great. Hmm? No way."
    assert smart_truncate(text, 12) == "Wow! Great." + MARKER


def test_sentence_fallback_newline_terminated():
    assert smart_truncate("Ends here.\nAnd more text", 12) == "Ends here." + MARKER


def test_word_fallback():
    text = "one two three four five"
    assert smart_truncate(text, 9) == "one two" + MARKER


def test_hard_cut():
    assert smart_truncate("abcdefghijkl", 5) == "abcde" + MARKER


def test_marker_appended_exactly_once():
    text = "alpha\n\nbeta\n\ngamma\n\ndelta"
    result = smart_truncate(text, 20)
    assert result.count(MARKER) == 1
    assert result.endswith(MARKER)


def test_ladder_path_marks_truncated_content(fresh_cache):
    """Regression: the ladder's HTTP fast path must run scraped content
    through smart_truncate, not a hard slice.

    Exercises the real ladder path at unit level: scrape_many() ->
    _resolve_fetch_http() -> fetch_http() (webget/http.py). The fetcher now
    caps markdown with smart_truncate instead of markdown[:max_chars], so a
    page that overflows the limit must come back already marked, and that
    mark must survive to the final result (the CLI no longer re-slices it off).

    max_chars is kept comfortably above the 100-char "thin content" floor so
    the ladder reports success and returns the (truncated, marked) markdown.
    """
    server = fresh_cache
    # /normal serves LONG_BODY (~1.4KB of repeating sentences) > max_chars.
    url = server.url("/normal")
    max_chars = 300
    res = asyncio.run(
        webget.scrape_many([url], max_chars=max_chars, strategy="http", no_cache=True)
    )
    out = res[url]
    assert out["status"] == "success", out.get("error")
    md = out["markdown"]
    assert md.endswith(MARKER), "ladder output must carry the truncation marker"
    assert md.count(MARKER) == 1
