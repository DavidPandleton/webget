"""Multi-engine search + atomic JSON helpers for webget.

The installed ``ddgs`` package is a metasearch ("Dux Distributed Global
Search"): it aggregates results from several KEYLESS web engines
(brave, duckduckgo, google, mojeek, startpage, wikipedia, yahoo, yandex
and more). Engine selection is a per-call ``backend`` kwarg on
``DDGS().text()`` - comma-delimited for a subset, ``"auto"`` for all.

Engine names come from ddgs's internal registry and can change per
release, so they are read at runtime and validated here. An unknown name
degrades to ``"auto"`` rather than raising: a typo should not kill a
search, and ddgs itself falls back to auto for unknown backends.
"""

from __future__ import annotations

import json
import os
import tempfile


def known_engines():
    """Return ddgs's registered text-engine names (runtime, never hardcoded).

    Empty list when ddgs is missing or its registry shape changes, in
    which case engine selection silently degrades to auto.
    """
    try:
        from ddgs.engines import ENGINES
    except Exception:  # noqa: BLE001 - best effort, never block search
        return []
    try:
        return sorted(ENGINES["text"].keys())
    except Exception:  # noqa: BLE001
        return []


def _resolve_engine(engine):
    """Validate a user engine spec, returning a safe ``backend`` value.

    ``None``/``""`` -> ``None`` (ddgs default = auto, unchanged behavior).
    Valid names -> comma-joined string. Anything unknown -> ``"auto"``
    with a best-effort warning, so a typo degrades instead of failing.
    """
    if not engine:
        return None
    engine = str(engine).strip()
    if not engine:
        return None
    if engine in ("auto", "all"):
        return "auto"
    names = [e.strip() for e in engine.split(",") if e.strip()]
    known = set(known_engines())
    if not known:
        # registry unavailable: trust ddgs to warn + fall back to auto
        return engine
    valid = [e for e in names if e in known]
    if not valid:
        _warn(
            f"unknown search engine(s): {engine} - using auto (known: {', '.join(sorted(known))})"
        )
        return "auto"
    if len(valid) != len(names):
        bad = [e for e in names if e not in known]
        _warn(
            f"ignoring unknown search engine(s): {', '.join(bad)} "
            f"(known: {', '.join(sorted(known))})"
        )
    return ",".join(valid)


def _warn(msg):
    """Best-effort stderr warning (import cycle safe)."""
    import sys

    try:
        sys.stderr.write(f"warning: {msg}\n")
        sys.stderr.flush()
    except OSError:
        pass


def search(query, n=5, engine=None):
    """Search the web via the ddgs metasearch and normalize the results.

    engine: None/"auto"/"all" = every engine (ddgs default), or a
    comma-delimited subset like "brave,duckduckgo". Unknown names are
    dropped with a warning; if none are known the call degrades to auto.
    """
    from ddgs import DDGS

    kwargs = {}
    backend = _resolve_engine(engine)
    if backend is not None:
        kwargs["backend"] = backend
    return [
        {"title": r["title"], "url": r["href"], "snippet": r.get("body", "")}
        for r in DDGS().text(query, max_results=n, **kwargs)
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
