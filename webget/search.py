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

# How many alternative engines to try after the requested one fails.
# Small on purpose: worst-case latency is roughly (1 + this) x timeout, and
# an agent calling search in a loop cannot afford a 9-engine timeout pile.
# Three is enough to survive one engine being blocked without turning a
# slow query into a very slow one.
MAX_FAILOVER_ATTEMPTS = 3


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


def _failover_chain(first):
    """Ordered engines to try when `first` fails: the rest of the registry.

    Ordered deterministically (registry order) excluding the one already
    tried. Returns [] when the registry is unavailable, in which case there
    is nothing to fail over to and the original error propagates.

    NOTE: this is deliberately NOT ranked by the 2026-09-18 benchmark.
    That benchmark showed only 'brave' reliable from the author's host, but
    engine health is host- and time-specific, so ranking it into the code
    would bake one machine's ISP filtering into every install.
    """
    known = known_engines()
    if not known:
        return []
    tried = {e.strip() for e in str(first or "").split(",") if e.strip()}
    if not tried:
        return []
    return [e for e in known if e not in tried]


class SearchError(RuntimeError):
    """Search failed on every engine, with provenance attached.

    Carries `.provenance` so a caller catching this still learns which
    engines were tried and why the first one failed, instead of only
    getting a bare message.
    """

    def __init__(self, message, provenance=None):
        super().__init__(message)
        self.provenance = provenance or {}


def search_with_provenance(query, n=5, engine=None):
    """Search and return ``(results, provenance)``.

    Provenance is per-CALL, not per-result, because ddgs merges every
    engine's hits into one list before returning (ddgs/results.py:
    TextResult carries only title/href/body, so the originating engine is
    discarded upstream). Per-call is what matters for failover anyway: it
    records which engine actually answered versus which was requested.

    provenance keys:
        requested  - what the caller asked for ("auto" when unspecified)
        engine     - the engine that returned results, or None if none did
        failed_over- True when `engine` differs from `requested`
        tried      - every engine attempted, in order
        first_error- string form of the first failure, or None
    """
    from ddgs import DDGS

    requested = engine if engine else "auto"
    backend = _resolve_engine(engine)
    kwargs = {} if backend is None else {"backend": backend}
    tried = []
    first_error = None

    def _one(kw, label):
        tried.append(label)
        return [
            {"title": r["title"], "url": r["href"], "snippet": r.get("body", "")}
            for r in DDGS().text(query, max_results=n, **kw)
        ]

    def _prov(answered):
        # failed_over means "something other than the request was tried".
        # Deriving it from `answered` alone was wrong: when every engine
        # fails there is no answering engine, yet failover may well have
        # been attempted, and a caller needs to know that.
        alternates = [t for t in tried if t != label]
        return {
            "requested": requested,
            "engine": answered,
            "failed_over": bool(alternates),
            "tried": list(tried),
            "first_error": str(first_error) if first_error is not None else None,
        }

    label = backend if backend is not None else "auto"
    try:
        got = _one(kwargs, label)
        if got:
            return got, _prov(label)
    except Exception as e:  # noqa: BLE001 - failover is the point
        first_error = e

    chain = _failover_chain(backend)
    if backend in (None, "auto"):
        chain = known_engines()
    for alt in chain[:MAX_FAILOVER_ATTEMPTS]:
        try:
            got = _one({"backend": alt}, alt)
        except Exception as e:  # noqa: BLE001 - try the next engine
            if first_error is None:
                first_error = e
            continue
        if got:
            _warn(f"engine '{label}' failed ({first_error or 'no results'}); fell back to '{alt}'")
            return got, _prov(alt)

    if first_error is not None:
        raise SearchError(str(first_error), provenance=_prov(None))
    return [], _prov(None)


def search(query, n=5, engine=None):
    """Search the web via the ddgs metasearch and normalize the results.

    engine: None/"auto"/"all" = every engine (ddgs default), or a
    comma-delimited subset like "brave,duckduckgo". Unknown names are
    dropped with a warning; if none are known the call degrades to auto.

    Failover: when the requested engine either raises OR returns an empty
    list, the remaining registered engines are tried and the first
    non-empty result wins. Both cases matter in practice: some engines
    raise (TLS/429), others silently return [] when they are blocked.

    The fallback is announced on stderr (never silent), and the ORIGINAL
    error is re-raised when nothing works, so the true cause survives
    instead of a generic "all engines failed".

    Returns a bare list for backward compatibility. Use
    ``search_with_provenance`` when you also need to know which engine
    actually answered (relevant since failover can substitute another one).
    """
    results, _ = search_with_provenance(query, n=n, engine=engine)
    return results


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
