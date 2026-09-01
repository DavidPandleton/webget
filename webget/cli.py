"""CLI entry point: argparse + command dispatch.

Usage (from --help):
  webget s "query" [n]           Search via DuckDuckGo (default 5)
  webget u "https://..."         Scrape URL -> markdown (HTTP fast path, falls back)
  webget su "query" [n]          Search + scrape top n results (default 3, parallel)
  webget s "q" | webget u -      Pipe: pass URL from search via stdin
                                 (multi-line stdin = batch scrape)
  webget login URL --profile X   Open browser, log in manually, persist session
  webget profiles [--json]       List profiles and session status
  webget logout URL --profile X  Clear auth for one domain, keep the rest
Aliases: search = s, fetch = u, search-fetch = su

Options:
  -c, --cookies FILE  Netscape-format cookie file (like curl -b)
  --profile NAME      Persistent browser profile (auth session) in
                      ~/.local/share/webget/profiles/<name>
  -H, --header "K: V" Extra header (repeatable)
  -n, --max-chars N   Max output chars (default: 10000 for u, 4000 for su)
  --limit N           Result count for s/su (default: 5 / 3)
  -t, --timeout N     Per-URL timeout in seconds (default: 20)
  --fresh             Bypass cache and re-scrape
  --ttl N             Cache TTL in seconds (default: 3600)
  --strategy S        Fetch strategy: auto|http|crawl4ai|firecrawl (default auto)
  --no-cache          Don't read or write the disk cache (private fetch)
  --concurrency N     Max concurrent fetches for batch runs (default: 10)
  --headless          Run login browser without a window (tests/automation)
  --json              Output results as JSON with metadata
                      (status/method/cached/auth)

Status values: success | login_required | challenge | blocked | error

Session management:
  webget login never stores passwords and never fills forms. You log in
  yourself in the opened browser window; webget just persists the session.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

from .cache import parse_cookie_file, parse_headers
from .ladder import scrape_many
from .profile import (
    _fmt_age,
    _login_flow,
    _logout_flow,
    _profile_root,
    _valid_site_url,
    _warn,
    list_profiles,
    profile_dir,
)
from .search import search

__doc__ = (
    'webget - local search + scrape, zero API keys, unlimited usage.\n'
    'Usage:\n'
    '  webget s "query" [n]           Search via DuckDuckGo (default 5)\n'
    '  webget u "https://..."         Scrape URL -> markdown (HTTP fast path, falls back)\n'
    '  webget su "query" [n]          Search + scrape top n results (default 3, parallel)\n'
    '  webget s "q" | webget u -      Pipe: pass URL from search via stdin\n'
    '                                 (multi-line stdin = batch scrape)\n'
    '  webget login URL --profile X   Open browser, log in manually, persist session\n'
    '  webget profiles [--json]       List profiles and session status\n'
    '  webget logout URL --profile X  Clear auth for one domain, keep the rest\n'
    'Aliases: search = s, fetch = u, search-fetch = su\n'
)


def parse_opts(args):
    """Extract options from positional args list."""
    cookies = None
    headers_list = []
    max_chars = None
    timeout = None
    fresh = False
    ttl = 3600
    json_out = False
    limit = None
    strategy = "auto"
    profile = None
    no_cache = False
    headless = False
    concurrency = None
    remaining = []
    i = 0
    while i < len(args):
        if args[i] in ("-c", "--cookies") and i + 1 < len(args):
            cookies = parse_cookie_file(args[i + 1])
            i += 2
        elif args[i] == "--profile" and i + 1 < len(args):
            profile = args[i + 1]
            i += 2
        elif args[i] == "--concurrency" and i + 1 < len(args):
            concurrency = int(args[i + 1])
            i += 2
        elif args[i] == "--no-cache":
            no_cache = True
            i += 1
        elif args[i] == "--headless":
            headless = True
            i += 1
        elif args[i] in ("-H", "--header") and i + 1 < len(args):
            headers_list.append(args[i + 1])
            i += 2
        elif args[i] in ("-n", "--max-chars") and i + 1 < len(args):
            max_chars = int(args[i + 1])
            i += 2
        elif args[i] == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1])
            i += 2
        elif args[i] in ("-t", "--timeout") and i + 1 < len(args):
            timeout = int(args[i + 1])
            i += 2
        elif args[i] == "--fresh":
            fresh = True
            i += 1
        elif args[i] == "--ttl" and i + 1 < len(args):
            ttl = int(args[i + 1])
            i += 2
        elif args[i] == "--strategy" and i + 1 < len(args):
            strategy = args[i + 1]
            i += 2
        elif args[i] == "--json":
            json_out = True
            i += 1
        else:
            remaining.append(args[i])
            i += 1
    return (
        remaining,
        cookies,
        parse_headers(headers_list),
        max_chars,
        timeout,
        fresh,
        ttl,
        json_out,
        limit,
        strategy,
        profile,
        no_cache,
        headless,
        concurrency,
    )


def cmd_profiles(json_out):
    if not os.path.isdir(_profile_root()):
        if json_out:
            print("{}")
        else:
            print("PROFILE     LAST USED     SIZE       STATUS\n(no profiles yet)")
        return
    profiles = list_profiles()
    if json_out:
        print(json.dumps({p["profile"]: p for p in profiles}, indent=2))
        return
    print(f"{'PROFILE':<12} {'LAST USED':<12} {'SIZE':>10}  STATUS")
    for p in profiles:
        print(
            f"{p['profile']:<12} {_fmt_age(p['last_used']):<12} "
            f"{p['size'] / 1024 / 1024:>8.1f} MB  {p['status']}"
        )


def cmd_login(site, profile, headless):
    ok, host = _valid_site_url(site)
    if not ok:
        print(f"error: invalid site URL: {host}")
        return 2
    if not profile:
        print("error: --profile NAME is required for login")
        return 2
    try:
        profile_dir(profile)
    except SystemExit as e:
        print(f"error: {e}")
        return 2
    print(f"webget login: profile '{profile}' for {host}")
    asyncio.run(_login_flow(site, profile, headless))
    return 0


def cmd_logout(site, profile):
    if not profile:
        print("error: --profile NAME is required for logout")
        return 2
    try:
        profile_dir(profile)
    except SystemExit as e:
        print(f"error: {e}")
        return 2
    ok, host_or_err = _valid_site_url(site)
    if not ok:
        print(f"error: invalid site URL: {host_or_err}")
        return 2
    ok, removed = asyncio.run(_logout_flow(site, profile))
    if not ok:
        print(f"error: {removed}")
        return 1
    print(f"Logged out {host_or_err} from profile '{profile}' ({removed} cookies cleared).")
    print("Other domains in this profile were left untouched.")
    return 0


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return

    (
        args,
        cookies,
        headers,
        max_chars_override,
        timeout_override,
        fresh,
        ttl,
        json_out,
        limit,
        strategy,
        profile,
        no_cache,
        headless,
        concurrency,
    ) = parse_opts(args)

    if concurrency is not None and concurrency < 1:
        print("error: --concurrency must be >= 1")
        sys.exit(2)

    if not args:
        print(__doc__)
        return

    if profile:
        try:
            os.makedirs(profile_dir(profile), exist_ok=True)
        except OSError as e:
            _warn(f"cannot create profile dir for '{profile}': {e}")

    cmd = args[0]
    if cmd == "search":
        cmd = "s"
    elif cmd == "fetch":
        cmd = "u"
    elif cmd == "search-fetch":
        cmd = "su"
    q = args[1] if len(args) > 1 else ""

    if cmd == "login":
        sys.exit(cmd_login(q, profile, headless))
    elif cmd == "profiles":
        cmd_profiles(json_out)
        return
    elif cmd == "logout":
        sys.exit(cmd_logout(q, profile))

    if cmd == "s":
        n = limit or (int(args[2]) if len(args) > 2 else 5)
        try:
            results = search(q, n=n)
        except Exception as e:  # noqa: BLE001 - surface a clean error, not a traceback
            print(f"error: search failed: {e}")
            sys.exit(1)
        for i, r in enumerate(results):
            print(f"{i + 1}. {r['title']}\n   {r['url']}\n   {r['snippet'][:200]}\n")

    elif cmd == "u":
        if q == "-":
            urls = [line.strip() for line in sys.stdin if line.strip()]
        else:
            urls = [q]
        max_chars = max_chars_override or 10000
        timeout = timeout_override or 20
        res = asyncio.run(
            scrape_many(
                urls,
                max_chars=max_chars,
                per_url_timeout=timeout,
                cookies=cookies,
                headers=headers,
                ttl=ttl,
                fresh=fresh,
                strategy=strategy,
                profile=profile,
                no_cache=no_cache,
                max_concurrency=concurrency,
            )
        )
        if json_out:
            print(json.dumps(res, indent=2))
            return
        for u_ in urls:
            r = res.get(u_, {})
            tag = r.get("method", "")
            stat = r.get("status", "")
            if stat != "success":
                auth = r.get("auth") or {}
                print(f"# {u_}  [{stat}/{tag}]")
                print(f"status={stat}")
                print(f"profile={auth.get('profile') or 'none'}")
                print(f"message={r.get('error') or 'unknown'}")
                continue
            print(f"# {r.get('title', '')}  [{stat}/{tag}]")
            if r.get("error"):
                print(f"ERROR: {r['error']}")
            print(f"{r.get('markdown', '')}\n")

    elif cmd == "su":
        n = limit or (int(args[2]) if len(args) > 2 else 3)
        max_chars = max_chars_override or 4000
        timeout = timeout_override or 20
        try:
            results = search(q, n=n)
        except Exception as e:  # noqa: BLE001 - surface a clean error, not a traceback
            print(f"error: search failed: {e}")
            sys.exit(1)
        urls = [r["url"] for r in results]
        scraped = asyncio.run(
            scrape_many(
                urls,
                max_chars=max_chars,
                per_url_timeout=timeout,
                cookies=cookies,
                headers=headers,
                ttl=ttl,
                fresh=fresh,
                strategy=strategy,
                profile=profile,
                no_cache=no_cache,
                max_concurrency=concurrency,
            )
        )
        if json_out:
            out = {}
            for i, r in enumerate(results):
                got = scraped.get(r["url"], {})
                out[r["url"]] = {
                    "rank": i + 1,
                    "search_title": r["title"],
                    "snippet": r.get("snippet", ""),
                    "scrape_title": got.get("title", ""),
                    "markdown": got.get("markdown", ""),
                    "status": got.get("status", ""),
                    "method": got.get("method", ""),
                    "cached": got.get("cached", False),
                    "attempts": got.get("attempts", 0),
                    "error": got.get("error"),
                    "auth": got.get("auth")
                    or {"profile": None, "authenticated": None, "state": got.get("status", "")},
                }
            print(json.dumps(out, indent=2))
            return
        for i, r in enumerate(results):
            got = scraped.get(r["url"], {})
            stat = got.get("status", "")
            method = got.get("method", "")
            err = got.get("error") or ""
            fail = stat != "success"
            print(
                f"\n{'=' * 60}\n## {i + 1}. {r['title']}  [{stat}/{method}]{'  ' + err if err else ''}\n{'=' * 60}"
            )
            print(f"URL: {r['url']}")
            print(got.get("markdown", "")[:max_chars] if not fail else "(no content)")

    else:
        print("Unknown command:", cmd)


if __name__ == "__main__":
    main()