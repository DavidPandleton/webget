"""DuckDuckGo search + atomic JSON helpers for webget."""

from __future__ import annotations

import json
import os
import tempfile


def search(query, n=5):
    from ddgs import DDGS

    return [
        {"title": r["title"], "url": r["href"], "snippet": r.get("body", "")}
        for r in DDGS().text(query, max_results=n)
    ]


def _read_json(path):
    """Sync helper for to_thread: read + parse JSON, raises on bad data."""
    with open(path) as f:
        return json.load(f)


def _write_json(path, data):
    """Sync helper for to_thread: atomic JSON write (unique tmp + rename)."""
    fd, tmp = tempfile.mkstemp(
        dir=os.path.dirname(path), prefix=os.path.basename(path) + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
