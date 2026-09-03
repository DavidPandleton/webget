"""Scrape ladder: orchestrates HTTP -> Crawl4AI -> Firecrawl in series.

Each step is tried in order; success promotes to cache + return, failure
is recorded with reasons and the ladder escalates. Per-domain strategy
memory promotes a known-good strategy to the front of the ladder so
subsequent 'auto' runs skip likely-failures.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from collections import Counter
from urllib.parse import urlparse

from . import firecrawl as _firecrawl_module
from . import http as _http_module
from . import ssrf as _ssrf_module
from .cache import cache_get, cache_put
from .http import ResponseTooLarge
from .profile import (
    _auth_state,
    _effective_cookies,
    profile_dir,
    profile_state_path,
)
from .ssrf import _is_private_target as _real_is_private_target


def _warn(msg):
    """Print a warning to stderr so stdout (JSON) stays clean."""
    print(f"webget: warning: {msg}", file=sys.stderr)


# Default cap on concurrent fetches per ladder pass. Unbounded gather on a
# 500-URL batch would open 500 connections (and later 500 browser pages).
_DEFAULT_CONCURRENCY = 10


# How long a per-domain strategy memory entry stays trusted. Sites change
# (a JS-heavy page may start server-rendering); a stale preference would
# skip the cheap HTTP path forever, so entries expire.
_STRATEGY_MEMORY_TTL = 3600 * 24 * 14


def _ladder(strategy, key):
    steps = []
    if strategy in ("auto", "http"):
        steps.append("http")
    if strategy in ("auto", "crawl4ai"):
        steps.append("crawl4ai")
    if strategy in ("auto", "firecrawl") and key:
        steps.append("firecrawl")
    elif strategy == "firecrawl" and not key:
        raise SystemExit("WEBGET_FIRECRAWL_KEY not set")
    if not steps:
        raise SystemExit(f"unknown strategy: {strategy}")
    return steps


def _strategy_memory_path():
    # Re-read CACHE_DIR via cache._cache_dir() so monkeypatches applied
    # to webget_cli.CACHE_DIR (test fixture hook) take effect here too.
    from .cache import _cache_dir

    return os.path.join(_cache_dir(), "strategy_memory.json")


def _load_strategy_memory():
    p = _strategy_memory_path()
    try:
        with open(p) as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    # Normalize to {domain: {"method": str, "ts": float}}. Entries older
    # than the expiry window are dropped: a site that once needed a
    # browser may serve plain HTML today (server-side rendering changes),
    # and a stale "crawl4ai" preference would skip the cheap HTTP path
    # forever. Plain-string values are the pre-expiry format; treat them
    # as fresh so an upgrade does not wipe a working memory.
    out = {}
    now = time.time()
    for domain, val in raw.items():
        if isinstance(val, str):
            out[domain] = {"method": val, "ts": now}
        elif isinstance(val, dict) and isinstance(val.get("method"), str):
            ts = val.get("ts", 0)
            if now - ts <= _STRATEGY_MEMORY_TTL:
                out[domain] = {"method": val["method"], "ts": ts}
    return out


def _save_strategy_memory(memory):
    p = _strategy_memory_path()
    try:
        from .cache import _cache_dir

        os.makedirs(_cache_dir(), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), prefix="strategy_memory.", suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(memory, f)
        os.replace(tmp, p)
    except OSError:
        pass  # best-effort


def _learn_strategy(domain, method):
    """Record that `method` succeeded for `domain`.

    Called after each successful fetch so future 'auto' ladder runs
    can try the known-good strategy first, skipping steps that are
    likely to fail (e.g. HTTP for a JS-heavy single-page app).
    Only writes when the value actually changes.
    """
    memory = _load_strategy_memory()
    if (memory.get(domain) or {}).get("method") == method:
        return
    memory[domain] = {"method": method, "ts": time.time()}
    _save_strategy_memory(memory)


def _reorder_steps_by_domain(steps, url):
    """Reorder ladder steps so the domain's preferred strategy comes first.

    When strategy='auto' and we have a memory of past success for this
    domain, promote that method to the front so the ladder skips likely
    failures. Unchanged when memory is empty or strategy is explicit.
    """
    if len(steps) <= 1:
        return steps
    domain = urlparse(url).hostname or ""
    if not domain:
        return steps
    memory = _load_strategy_memory()
    entry = memory.get(domain)
    preferred = entry.get("method") if entry else None
    if not preferred or preferred not in steps:
        return steps
    return [preferred] + [s for s in steps if s != preferred]


def _normalize_hit(hit):
    return {
        "title": hit.get("title", ""),
        "markdown": hit.get("markdown", ""),
        "status": "success",
        "method": "cache",
        "cached": True,
        "attempts": 1,
        "error": None,
        "auth": hit.get("auth") or {"profile": None, "authenticated": None, "state": "success"},
        "reasons": [],
    }


async def _crawl4ai_once(crawler, cfg, url, per_url_timeout):
    async def attempt():
        task = asyncio.create_task(crawler.arun(url=url, config=cfg))
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        r = await asyncio.wait_for(task, timeout=per_url_timeout)
        if not getattr(r, "success", True):
            raise RuntimeError(getattr(r, "error_message", None) or "crawl failed")
        return r

    # Policy: retry once on timeout / transient failures; don't retry auth errors.
    def _no_retry(e):
        msg = str(e).lower()
        return any(
            m in msg for m in ("401", "403", "challenge", "captcha", "access denied", "forbidden")
        )

    try:
        r = await attempt()
        # NOTE: crawl4ai's browser manager seeds every context with a
        # "cookiesEnabled=true" cookie (anti-cookie-banner heuristic), so
        # endpoints that gate on ANY cookie presence look authenticated to
        # the browser path. r.status_code is the container's view (rewound
        # to 200 on success); there is no events API for the true nav
        # status, so auth classification leans on content markers via
        # _auth_state. Real sites gate on a named session cookie, which
        # the injected one does not satisfy.
        return {
            "title": (r.metadata or {}).get("title", ""),
            "markdown": (r.markdown or ""),
            "status_code": getattr(r, "status_code", None),
            "html": (r.html or "")[:8000],
        }
    except Exception as e:
        if _no_retry(e):
            raise
        await asyncio.sleep(2)
        try:
            r2 = await attempt()
            return {
                "title": (r2.metadata or {}).get("title", ""),
                "markdown": (r2.markdown or ""),
                "status_code": getattr(r2, "status_code", None),
                "html": (r2.html or "")[:8000],
            }
        except Exception:  # noqa: BLE001 - surface original error, retry already done
            raise RuntimeError(str(e)) from e


def _resolve_crawl4ai_once():
    """Return the crawl4ai-once implementation, honoring test patches.

    Tests patch `webget._crawl4ai_once` on the shim module to swap in a
    fake. The original single-file webget read this attribute from the
    SAME module that defined it; in the package layout the function lives
    in `webget.ladder`, but tests still patch the shim. Resolve the
    attribute dynamically so the patch reaches the call site.
    """
    try:
        import webget_cli as _shim

        fn = getattr(_shim, "_crawl4ai_once", None)
        if fn is not None and fn is not _crawl4ai_once:
            return fn
    except ImportError:
        pass
    return _crawl4ai_once


def _resolve_fetch_http():
    """Return the http fetcher, honoring test patches on the shim.

    Same rationale as _resolve_crawl4ai_once: tests do
    `webget.fetch_http = spy` on the shim; the package layout would
    otherwise bind the symbol at import time and miss the patch.
    """
    try:
        import webget_cli as _shim

        fn = getattr(_shim, "fetch_http", None)
        if fn is not None and fn is not _http_module.fetch_http:
            return fn
    except ImportError:
        pass
    return _http_module.fetch_http


def _resolve_is_private_target():
    """Return the SSRF policy function, honoring test patches on the shim.

    Tests do `webget._is_private_target = spy` (or a wrapper); the package
    layout would otherwise bind the symbol at import time and miss the
    patch.
    """
    try:
        import webget_cli as _shim

        fn = getattr(_shim, "_is_private_target", None)
        if fn is not None and fn is not _real_is_private_target:
            return fn
    except ImportError:
        pass
    return _real_is_private_target


def _resolve_private_ip_for():
    """Return the private-IP detail function, honoring test patches.

    Tests do `webget._private_ip_for = spy` to control which address the
    error message names; same package-vs-shim story as the other
    resolvers.
    """
    try:
        import webget_cli as _shim

        fn = getattr(_shim, "_private_ip_for", None)
        if fn is not None and fn is not _ssrf_module._private_ip_for:
            return fn
    except ImportError:
        pass
    return _ssrf_module._private_ip_for


def _terminal_state(reasons, profile):
    """Pick final state from ladder reasons. Priority: challenge > login_required > blocked > error."""
    order = ("challenge", "login_required", "blocked", "error")
    for wanted in order:
        for state, method, detail in reasons:
            if state == wanted:
                authenticated = None
                if state == "login_required":
                    authenticated = False
                return state, authenticated, detail, method
    first = reasons[0] if reasons else None
    method = first[1] if first else ""
    return "error", None, "; ".join(d for _, _, d in reasons), method


def _auth_message(state, profile):
    if state == "login_required":
        return f"Authenticated session appears expired or unavailable (profile={profile or 'none'})"
    if state == "challenge":
        return "Site requires human verification; automatic acquisition stopped"
    if state == "blocked":
        return "Access blocked by the site (403/429 or access-denied page)"
    return "Unknown fetch failure"


async def scrape_many(
    urls,
    max_chars=6000,
    per_url_timeout=20,
    cookies=None,
    headers=None,
    ttl=3600,
    fresh=False,
    strategy="auto",
    profile=None,
    no_cache=False,
    max_concurrency=None,
    retry_transient=False,
):
    steps = _ladder(strategy, _firecrawl_module.firecrawl_key())
    # Per-domain strategy memory: when strategy is "auto" and we have
    # past success data for the batch's majority domain, reorder steps
    # so the known-good strategy is tried first, skipping likely failures.
    if strategy == "auto" and urls:
        domains = [urlparse(u).hostname for u in urls if urlparse(u).hostname]
        if domains:
            majority = Counter(domains).most_common(1)[0][0]
            steps = _reorder_steps_by_domain(steps, f"https://{majority}/")
    results = {}
    missing = []
    # Deduplicate: identical URLs must not cause duplicate work or races.
    seen = set()
    candidates = []
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        candidates.append(u)
    # SSRF guard applies to every strategy, not just the HTTP fast path:
    # never hand a private target to the browser or to Firecrawl either.
    # The check resolves hostnames (blocking); run them concurrently in
    # worker threads with a hard cap so one sick DNS server cannot stall
    # the event loop and stretch every timer in the batch. The semaphore
    # bounds in-flight lookups: the default executor has ~32 threads, and
    # an unbounded gather would queue later URLs past their own deadline.
    dns_sem = asyncio.Semaphore(32)

    async def _ssrf_verdict(u):
        async with dns_sem:
            try:
                blocked = await asyncio.wait_for(
                    asyncio.to_thread(_resolve_is_private_target(), u), per_url_timeout
                )
            except TimeoutError:
                return u, f"DNS resolution exceeded {per_url_timeout}s"
            if not blocked:
                return u, None
            # Name the offending address when we can (best-effort: callers
            # that patch _is_private_target may not have a matching
            # _private_ip_for).
            ip = await asyncio.to_thread(_resolve_private_ip_for(), u)
            return u, f"resolves to private address {ip}" if ip else "targets a private address"

    verdicts = dict(await asyncio.gather(*(_ssrf_verdict(u) for u in candidates)))
    for u in candidates:
        bad = verdicts.get(u)
        if bad:
            results[u] = {
                "title": "",
                "markdown": "",
                "status": "error",
                "method": "",
                "cached": False,
                "attempts": 0,
                "error": f"blocked by SSRF guard: {u} {bad}",
                "auth": {"profile": profile, "authenticated": None, "state": "error"},
                "reasons": [{"state": "error", "method": "", "detail": bad}],
            }
            continue
        hit = None if no_cache else cache_get(u, cookies, headers, max_chars, ttl, fresh, profile)
        if hit:
            results[u] = _normalize_hit(hit)
        else:
            missing.append(u)
    if not missing:
        return results

    sem = asyncio.Semaphore(max_concurrency or _DEFAULT_CONCURRENCY)

    attempts = {u: 0 for u in missing}
    reasons = {u: [] for u in missing}
    pending = list(missing)

    async def record(url, method, res=None, exc=None):
        """Classify one strategy result; return success dict or None (keep climbing)."""
        attempts[url] += 1
        if exc is not None or res is None:
            # Some httpx exceptions (ReadTimeout, ConnectTimeout) stringify
            # to the empty string; a bare "" error is useless to callers,
            # so fall back to the exception type name.
            detail = str(exc) if exc is not None else "no result"
            if not detail.strip():
                detail = type(exc).__name__
            reasons[url].append(("error", method, detail))
            return None
        state, authenticated = _auth_state(res, profile)
        if state == "success" and len((res.get("markdown") or "").strip()) >= 100:
            auth = {"profile": profile, "authenticated": authenticated, "state": state}
            # Record which strategy won for this domain so future 'auto'
            # batches can try it first (per-domain strategy memory).
            domain = urlparse(url).hostname
            if domain:
                _learn_strategy(domain, method)
            out = {
                "title": res.get("title", ""),
                "markdown": res.get("markdown", ""),
                "status": "success",
                "method": method,
                "cached": False,
                "attempts": attempts[url],
                "error": None,
                "auth": auth,
                # Consistent shape: failures carry the ladder chain; successes
                # carry an empty list (never null) so consumers can always
                # iterate reasons without a None check.
                "reasons": [],
            }
            if not no_cache:
                cache_put(url, cookies, headers, max_chars, out, profile)
            return out
        if state == "success":
            reasons[url].append((state, method, "content too thin"))
        else:
            reasons[url].append((state, method, _auth_message(state, profile)))
        return None

    # Pass 1: HTTP fast path - no browser involved.
    if "http" in steps:

        async def http_one(url):
            async with sem:
                try:
                    res = await _resolve_fetch_http()(
                        url,
                        max_chars,
                        _effective_cookies(cookies, profile),
                        headers,
                        timeout=per_url_timeout,
                    )
                    return url, await record(url, "http", res=res)
                except TimeoutError:
                    return url, await record(url, "http", exc=TimeoutError("timeout"))
                except ResponseTooLarge as e:
                    # TERMINAL: the body exceeded MAX_RESPONSE_BYTES. A browser
                    # pass would re-download the same giant body through
                    # Chromium, so escalating is pure waste. Report it as a
                    # final error and drop the URL from the ladder.
                    return url, {
                        "title": "",
                        "markdown": "",
                        "status": "error",
                        "method": "http",
                        "cached": False,
                        "attempts": 1,
                        "error": str(e),
                        "auth": {
                            "profile": profile,
                            "authenticated": None,
                            "state": "error",
                        },
                        "reasons": [{"state": "error", "method": "http", "detail": str(e)}],
                    }
                except Exception as e:  # noqa: BLE001 - record reason, ladder continues
                    return url, await record(url, "http", exc=e)

        for url, out in await asyncio.gather(*(http_one(u) for u in pending)):
            if out:
                results[url] = out
        pending = [u for u in pending if u not in results]

        # Optional retry pass for transient timeouts on the fast HTTP path
        if retry_transient and pending and "http" in steps:
            transient_candidates = [
                u
                for u in pending
                if reasons[u]
                and reasons[u][-1][0] == "error"
                and "timeout" in str(reasons[u][-1][2]).lower()
            ]
            if transient_candidates:
                retry_timeout = int(per_url_timeout * 1.5)

                async def http_retry_one(u):
                    async with sem:
                        try:
                            res = await _resolve_fetch_http()(
                                u,
                                max_chars,
                                _effective_cookies(cookies, profile),
                                headers,
                                timeout=retry_timeout,
                            )
                            return u, await record(u, "http", res=res)
                        except Exception as e:  # noqa: BLE001
                            return u, await record(u, "http", exc=e)

                for url, out in await asyncio.gather(
                    *(http_retry_one(u) for u in transient_candidates)
                ):
                    if out:
                        results[url] = out
                pending = [u for u in pending if u not in results]

    # Pass 2: Crawl4AI browser - only launched if something still needs it.
    if pending and "crawl4ai" in steps:
        try:
            from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
        except ImportError:
            _warn(
                "crawl4ai is not installed; install it with "
                "'pip install webget-cli[browser]' or 'uv tool install "
                "webget-cli --with webget-cli[browser]'"
            )
            for url in pending:
                reasons[url].append(("error", "crawl4ai", "crawl4ai not installed"))
            # Don't clear pending: keep URLs so the ladder can continue
            # (e.g. firecrawl fallback) and so terminal state is written below.
        else:
            if pending:
                bc = BrowserConfig(
                    use_persistent_context=bool(profile),
                    user_data_dir=profile_dir(profile) if profile else None,
                    cookies=cookies or None,
                    headers=headers or None,
                )
                cfg = CrawlerRunConfig()
                async with AsyncWebCrawler(config=bc, verbose=False) as crawler_ctx:
                    await _ssrf_module._guard_browser_routes(crawler_ctx)

                    async def crawl_one(url):
                        async with sem:
                            try:
                                res = await _resolve_crawl4ai_once()(
                                    crawler_ctx, cfg, url, per_url_timeout
                                )
                                res["markdown"] = res.get("markdown", "")[:max_chars]
                                return url, await record(url, "crawl4ai", res=res)
                            except TimeoutError:
                                return url, await record(
                                    url, "crawl4ai", exc=TimeoutError("timeout")
                                )
                            except Exception as e:  # noqa: BLE001 - record reason, ladder continues
                                return url, await record(url, "crawl4ai", exc=e)

                    for url, out in await asyncio.gather(*(crawl_one(u) for u in pending)):
                        if out:
                            results[url] = out
                    pending = [u for u in pending if u not in results]
                    if profile:
                        try:
                            # Persist session so future HTTP-path fetches can reuse it.
                            # Note: crawl4ai's export_storage_state is broken in 0.9.2
                            # (accesses self.default_context on the strategy, which
                            # lives on browser_manager instead) - go direct.
                            bm = crawler_ctx.crawler_strategy.browser_manager
                            if bm and bm.default_context is not None:
                                await bm.default_context.storage_state(
                                    path=profile_state_path(profile)
                                )
                            else:
                                _warn(
                                    f"profile '{profile}' used but no browser context "
                                    "available; session will NOT be persisted"
                                )
                        except Exception as e:  # noqa: BLE001 - warn, don't crash the batch
                            _warn(f"failed to persist profile session for '{profile}': {e}")

    # Pass 3: Firecrawl - optional cloud escape hatch.
    if pending and "firecrawl" in steps:

        async def fc_one(url):
            async with sem:
                try:
                    res = await _firecrawl_module.fetch_firecrawl(
                        url, max_chars, _firecrawl_module.firecrawl_key(), timeout=per_url_timeout
                    )
                    return url, await record(url, "firecrawl", res=res)
                except Exception as e:  # noqa: BLE001 - record reason, ladder continues
                    return url, await record(url, "firecrawl", exc=e)

        for url, out in await asyncio.gather(*(fc_one(u) for u in pending)):
            if out:
                results[url] = out
        pending = [u for u in pending if u not in results]

    # Ladder exhausted for the rest: terminal state from all recorded reasons.
    for url in pending:
        state, authenticated, detail, method = _terminal_state(reasons[url], profile)
        auth = {"profile": profile, "authenticated": authenticated, "state": state}
        results[url] = {
            "title": "",
            "markdown": "",
            "status": state,
            "method": method,
            "cached": False,
            "attempts": attempts[url],
            "error": detail,
            "auth": auth,
            "reasons": [{"state": s, "method": m, "detail": d} for s, m, d in reasons[url]],
        }
    return results
