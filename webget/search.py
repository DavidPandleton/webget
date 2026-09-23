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
from typing import Protocol, runtime_checkable


@runtime_checkable
class SearchProvider(Protocol):
    """Minimal backend contract used by the normalized search pipeline."""

    def known_engines(self) -> list[str]:
        """Return backend names accepted by ``engine`` selection."""
        ...

    def text(self, query: str, max_results: int, **kwargs) -> list[dict]:
        """Return raw result mappings with title, href, and body fields."""
        ...


class DdgsSearchProvider:
    """Default embedded provider; imports ddgs only when a search runs."""

    def known_engines(self) -> list[str]:
        return known_engines()

    def text(self, query: str, max_results: int, **kwargs) -> list[dict]:
        from ddgs import DDGS

        return DDGS().text(query, max_results=max_results, **kwargs)


class SearxngSearchProvider:
    """Optional HTTP adapter for a configured SearXNG instance."""

    def __init__(self, endpoint: str | None = None, *, timeout: float = 20.0):
        self.endpoint = (endpoint or os.environ.get("WEBGET_SEARXNG_URL", "")).rstrip("/")
        self.timeout = timeout
        if not self.endpoint:
            raise ValueError("SearXNG endpoint is required")

    def known_engines(self) -> list[str]:
        return ["searxng"]

    def text(self, query: str, max_results: int, **kwargs) -> list[dict]:
        import httpx

        response = httpx.get(
            f"{self.endpoint}/search",
            params={"q": query, "format": "json", "categories": "general"},
            timeout=self.timeout,
            follow_redirects=False,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise TypeError("SearXNG response has no results list")
        rows = []
        for item in payload["results"][:max_results]:
            if not isinstance(item, dict) or not item.get("url"):
                continue
            rows.append(
                {
                    "title": item.get("title") or "",
                    "href": item["url"],
                    "body": item.get("content") or item.get("snippet") or "",
                }
            )
        return rows


def _default_provider() -> SearchProvider:
    return DdgsSearchProvider()


# Failover is bounded by TIME, not by a count of attempts.
#
# A count is a bad proxy for "have I tried enough". Three attempts is either
# too many (3 x 20s timeout = 60s, far past what a caller will wait) or too
# few (8 fast engines answered in 3s, yet engine 4 held the only working
# result). The deadline measures the thing that actually matters.
#
# 15s is chosen to sit just under the common 20s HTTP client timeout, so a
# caller that set its own timeout does not see webget outlive it. Override
# with WEBGET_FAILOVER_BUDGET_S.
FAILOVER_BUDGET_S = 15.0

# Always attempt at least this many alternates regardless of the clock. Two
# engines being blocked at once is common (the stratified benchmark saw whole
# groups fail together), and a budget that expired after one try would give up
# while a working engine was still one call away.
MIN_FAILOVER_ATTEMPTS = 2


def _failover_budget():
    """Seconds allowed for the failover phase. Env-overridable for callers."""
    raw = os.environ.get("WEBGET_FAILOVER_BUDGET_S")
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    return FAILOVER_BUDGET_S


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


def _resolve_engine(engine, provider: SearchProvider | None = None):
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
    known = set(provider.known_engines() if provider is not None else known_engines())
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


def _failover_chain(first, provider: SearchProvider | None = None):
    """Ordered engines to try when `first` fails: the rest of the registry.

    Ordered by LEARNED HEALTH (webget.health), best first, with a stable
    tiebreak on registry order for engines this install has never tried.

    This is deliberately NOT ranked by any benchmark baked into the source.
    The 0.13.0 benchmark showed only one or two engines reliable from the
    author's host, but engine health is host-, time- and query-specific (the
    stratified run found the ranking differs per query type), so a baked-in
    order would ship one machine's ISP filtering to every user. Health is
    learned locally at runtime and decays, so it adapts per install.

    Returns [] when the registry is unavailable, in which case there is
    nothing to fail over to and the original error propagates.
    """
    known = provider.known_engines() if provider is not None else known_engines()
    if not known:
        return []
    tried = {e.strip() for e in str(first or "").split(",") if e.strip()}
    if not tried:
        return []
    candidates = [e for e in known if e not in tried]
    try:
        from webget import health

        return health.order(candidates)
    except Exception:  # noqa: BLE001 - health is advisory, never load-bearing
        return candidates


class SearchError(RuntimeError):
    """Search failed on every engine, with provenance attached.

    Carries `.provenance` so a caller catching this still learns which
    engines were tried and why the first one failed, instead of only
    getting a bare message.
    """

    def __init__(self, message, provenance=None):
        super().__init__(message)
        self.provenance = provenance or {}


def search_with_provenance(
    query, n=5, engine=None, provider: SearchProvider | None = None
):
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
        health_ranked - True when the failover order came from learned health
                        rather than registry order, so a caller debugging a
                        surprising order knows where it came from
    """
    provider = provider or _default_provider()

    requested = engine if engine else "auto"
    backend = _resolve_engine(engine, provider)
    kwargs = {} if backend is None else {"backend": backend}
    tried = []
    first_error = None

    def _record(label, ok, latency):
        """Feed one outcome to the health ledger. Never load-bearing.

        A comma-delimited request ("brave,duckduckgo") is recorded against each
        named engine, not against the joined string. Recording the label as-is
        created a ledger key called "brave,duckduckgo", which scores nothing:
        the per-engine health used for ordering never saw the observation, and
        a caller passing a subset silently built a parallel set of dead keys.
        """
        try:
            from webget import health

            if not label or label in ("auto", "all"):
                return
            for name in str(label).split(","):
                name = name.strip()
                if name:
                    health.record(name, ok, latency)
        except Exception:  # noqa: BLE001, S110 - health must never break a search
            pass

    def _one(kw, label):
        import time as _time

        tried.append(label)
        t0 = _time.perf_counter()
        try:
            rows = list(provider.text(query, max_results=n, **kw) or [])
        except Exception:
            # A raise is a data point too: this engine is unhealthy right now.
            _record(label, False, _time.perf_counter() - t0)
            raise
        # An empty list counts as a failure for health purposes even though
        # ddgs did not raise - several engines are blocked silently, and
        # treating that as success would keep them ranked first.
        _record(label, bool(rows), _time.perf_counter() - t0)
        # Normalisasi dengan toleransi. `body` sudah lama memakai .get(),
        # tapi `title` dan `href` diakses langsung, sehingga SATU baris
        # cacat dari engine yang sehat melempar KeyError dan menggagalkan
        # SELURUH pencarian - 14 hasil bagus ikut hilang karena 1 baris
        # tanpa "title". Provider memang mengirim baris tidak lengkap
        # (ddgs memetakan beberapa backend dengan bentuk berbeda), jadi
        # lewati baris yang tidak punya tujuan, dan beri nilai kosong
        # untuk judul yang hilang.
        hasil = []
        for r in rows:
            href = r.get("href")
            if not href:
                # Tanpa URL, hasilnya tidak berguna bagi pemanggil
                # (tidak bisa dibuka). Lewati, jangan gagalkan semuanya.
                continue
            hasil.append(
                {
                    "title": r.get("title") or "",
                    "url": href,
                    "snippet": r.get("body") or "",
                }
            )
        return hasil

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
            "health_ranked": _health_ranked,
        }

    label = backend if backend is not None else "auto"

    # Decided BEFORE the first attempt, because provenance is built on the
    # success path too. Defining this after the first try raised
    # UnboundLocalError inside _prov, which the failover except swallowed: the
    # search still returned results, but every call took the failover path and
    # burned an extra engine. Silent, and on the most common path.
    _health_ranked = False
    try:
        from webget import health as _health

        _health_ranked = bool(_health.load())
    except Exception:  # noqa: BLE001 - health is advisory
        _health_ranked = False

    try:
        got = _one(kwargs, label)
        if got:
            return got, _prov(label)
    except Exception as e:  # noqa: BLE001 - failover is the point
        first_error = e

    chain = _failover_chain(backend, provider)
    if backend in (None, "auto"):
        chain = provider.known_engines()

    # Walk the chain until the time budget is spent, but never stop before
    # MIN_FAILOVER_ATTEMPTS. `deadline` starts now rather than at the top of
    # the function on purpose: the caller's requested engine already had its
    # own chance, and charging that time to the failover budget would make a
    # slow first attempt silently disable the safety net.
    import time as _time

    deadline = _time.monotonic() + _failover_budget()
    attempted = 0
    exhausted = False
    for alt in chain:
        if attempted >= MIN_FAILOVER_ATTEMPTS and _time.monotonic() >= deadline:
            exhausted = True
            break
        attempted += 1
        try:
            got = _one({"backend": alt}, alt)
        except Exception as e:  # noqa: BLE001 - try the next engine
            if first_error is None:
                first_error = e
            continue
        if got:
            _warn(f"engine '{label}' failed ({first_error or 'no results'}); fell back to '{alt}'")
            return got, _prov(alt)

    if exhausted:
        # Say so, rather than letting a budget-limited failure look like
        # "every engine is dead". They are different problems.
        _warn(
            f"failover budget of {_failover_budget():.0f}s exhausted after "
            f"{attempted} engine(s); {len(chain) - attempted} untried"
        )

    if first_error is not None:
        prov = _prov(None)
        prov["budget_exhausted"] = exhausted
        prov["untried"] = max(0, len(chain) - attempted)
        raise SearchError(str(first_error), provenance=prov)
    prov = _prov(None)
    prov["budget_exhausted"] = exhausted
    prov["untried"] = max(0, len(chain) - attempted)
    return [], prov


def search(query, n=5, engine=None, provider: SearchProvider | None = None):
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
    results, _ = search_with_provenance(query, n=n, engine=engine, provider=provider)
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
