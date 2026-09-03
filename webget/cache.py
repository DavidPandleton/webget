"""Cache + cookie/header parsing for webget.

Disk cache lives at CACHE_DIR and is keyed by (profile, url, max_chars,
sorted cookies, sorted headers) so semantically-identical states share one
entry. Writes are atomic (per-writer unique tmp + os.replace) and the cache
self-evicts via two-phase sweep when over the cap.

CACHE_DIR is resolved lazily through a getter that consults the
top-level webget package first, falling back to the package default.
This keeps `monkeypatch.setattr(webget_cli, "CACHE_DIR", ...)` working
in the test suite: the canonical location for the path is the shim's
module attribute, and package sub-modules follow whatever the shim
currently exposes.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time

# Package default; tests that patch webget_cli.CACHE_DIR take precedence
# because _cache_dir() looks up the parent package's attribute at call time.
_CACHE_DIR_DEFAULT = os.path.expanduser("~/.cache/webget")


def _cache_dir():
    """Resolve the active cache directory, honoring runtime overrides.

    Tests patch `webget_cli.CACHE_DIR` (the shim module attribute) to
    point at a tmp dir. Sub-modules in the package consult THIS function
    so the same override applies everywhere without circular imports.
    """
    try:
        import webget_cli as _shim

        return _shim.CACHE_DIR
    except (ImportError, AttributeError):
        return _CACHE_DIR_DEFAULT


# Backwards-compatible module attr: defaults to the package default, but
# assigning to `webget.cache.CACHE_DIR` only affects THIS module. Tests
# use `webget_cli.CACHE_DIR` (the shim) as the override hook, which is
# picked up via _cache_dir() above.
CACHE_DIR = _CACHE_DIR_DEFAULT

# Entries older than this can never be served by any reasonable ttl and are
# swept on write. 30 days matches the default CLI ttl.
_CACHE_SWEEP_TTL = 3600 * 24 * 30


def parse_cookie_file(path):
    """Parse Netscape cookie file -> Playwright-compatible cookie dicts."""
    cookies = []
    with open(os.path.expanduser(path)) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            domain, _domain_flag, path_, secure, expires, name, value = parts[:7]
            cookie = {
                "name": name,
                "value": value,
                "domain": domain,
                "path": path_,
                "secure": secure == "TRUE",
                "httpOnly": False,
            }
            if expires not in ("0", "Session", ""):
                try:
                    cookie["expires"] = int(expires)
                except ValueError:
                    pass
            cookies.append(cookie)
    return cookies


def parse_headers(raw_headers):
    """Parse list of 'Key: Value' strings into a dict."""
    headers = {}
    for h in raw_headers:
        if ":" in h:
            k, v = h.split(":", 1)
            headers[k.strip()] = v.strip()
    return headers


def _cache_path(url, cookies, headers, max_chars, profile=None):
    # Cookies and headers are sorted so semantically identical states (same
    # cookie set in different order) share one cache entry.
    ck = sorted(cookies or [], key=lambda c: (c.get("domain", ""), c.get("name", "")))
    hd = {k: v for k, v in sorted((headers or {}).items())}
    key = hashlib.sha1(
        f"{profile or 'public'}|{url}|{max_chars}|"
        f"{json.dumps(ck, sort_keys=True)}|"
        f"{json.dumps(hd, sort_keys=True)}".encode()
    ).hexdigest()
    return os.path.join(_cache_dir(), key + ".json")


def cache_get(url, cookies, headers, max_chars, ttl, fresh=False, profile=None):
    if fresh:
        return None
    p = _cache_path(url, cookies, headers, max_chars, profile)
    if not os.path.exists(p):
        return None
    if time.time() - os.path.getmtime(p) > ttl:
        return None
    try:
        with open(p) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def cache_put(url, cookies, headers, max_chars, data, profile=None):
    cache_dir = _cache_dir()
    os.makedirs(cache_dir, exist_ok=True)
    p = _cache_path(url, cookies, headers, max_chars, profile)
    tmp = None
    try:
        # Atomic write: each writer gets its OWN unique tmp file (mkstemp),
        # then an atomic rename. A shared "<path>.tmp" would let concurrent
        # writers interleave bytes in the same file, so the rename would
        # publish a corrupt document. mkstemp guarantees uniqueness per
        # writer and lives in the same directory (same filesystem, so
        # os.replace stays atomic).
        fd, tmp = tempfile.mkstemp(
            dir=os.path.dirname(p), prefix=os.path.basename(p) + ".", suffix=".tmp"
        )
        with os.fdopen(fd, "w") as f:
            json.dump({**data, "fetched_at": time.time()}, f)
        os.replace(tmp, p)
        tmp = None  # consumed by the rename
        # Eviction (lazy: only scans when actually over the cap, so the hot
        # write path stays cheap). Drop expired entries first - they can
        # never be served - then keep newest 80% if still over. The cap is
        # tunable via WEBGET_CACHE_MAX because a 500-file ceiling makes
        # bulk crawls (thousands of URLs) thrash: every write evicts
        # useful entries.
        try:
            cap = int(os.environ.get("WEBGET_CACHE_MAX", "5000"))
        except ValueError:
            cap = 5000
        try:
            files = [
                os.path.join(cache_dir, f)
                for f in os.listdir(cache_dir)
                if f.endswith(".json") and f != "strategy_memory.json"
            ]
            if len(files) > cap:
                now = time.time()
                live = []
                for fp in files:
                    try:
                        age = now - os.path.getmtime(fp)
                    except OSError:
                        continue
                    if age > _CACHE_SWEEP_TTL:
                        try:
                            os.remove(fp)
                        except OSError:
                            pass
                    else:
                        live.append(fp)
                if len(live) > cap:
                    live.sort(key=os.path.getmtime)
                    for f in live[: len(live) - int(cap * 0.8)]:
                        try:
                            os.remove(f)
                        except OSError:
                            pass
        except OSError:
            pass
    except OSError:
        pass  # cache is best-effort
    finally:
        if tmp is not None:
            try:
                os.remove(tmp)
            except OSError:
                pass
