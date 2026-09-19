"""Boundary-aware truncation for markdown content.

The CLI caps scraped content with a hard slice (markdown[:max_chars]), which
frequently cuts mid-sentence or mid-word. smart_truncate() instead cuts at
the last clean boundary that fits - paragraph, sentence, word - and marks the
result with an ellipsis so readers can tell the content was cut.
"""

from __future__ import annotations

import re

ELLIPSIS_MARKER = "\n\n[...truncated]"

# A sentence end: one of . ! ? immediately followed by a space or newline.
_SENTENCE_END_RE = re.compile(r"[.!?][ \n]")


def smart_truncate(text: str, limit: int) -> str:
    """Cut text to `limit` chars at the last clean boundary.

    Preference order: paragraph break (\\n\\n) -> sentence end (. ! ? followed
    by whitespace) -> word break (space) -> hard cut at `limit`. The ellipsis
    marker is appended whenever truncation occurs; text at or under the limit
    is returned unchanged.
    """
    if len(text) <= limit:
        return text

    cut = text.rfind("\n\n", 0, limit)
    if cut > 0:
        return text[:cut] + ELLIPSIS_MARKER

    cut = _last_match_end(_SENTENCE_END_RE, text[:limit])
    if cut > 0:
        return text[:cut].rstrip() + ELLIPSIS_MARKER

    cut = text.rfind(" ", 0, limit)
    if cut > 0:
        return text[:cut].rstrip() + ELLIPSIS_MARKER

    return text[:limit] + ELLIPSIS_MARKER


def _last_match_end(pattern: re.Pattern[str], text: str) -> int:
    """End index of the last match in `text`, or -1 when there is none."""
    last = -1
    for match in pattern.finditer(text):
        last = match.end()
    return last
