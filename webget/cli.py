"""CLI entry point: option parsing and command dispatch.

The user-facing help text is assigned to __doc__ further down, after the
imports. It used to live here as a module docstring and was then
overwritten, so the options it documented (--engine among them) were
invisible in --help for the whole 0.13.0 line.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from .cache import parse_cookie_file, parse_headers
from .crawler import crawl_site
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
from .search import search, search_with_provenance

__doc__ = (
    "webget - local search + scrape, zero API keys, unlimited usage.\n"
    "Usage:\n"
    '  webget s "query" [n]           Search the web via ddgs metasearch (default 5)\n'
    '  webget u "https://..."         Scrape URL -> markdown (HTTP fast path, falls back)\n'
    '  webget su "query" [n]          Search + scrape top n results (default 3, parallel)\n'
    '  webget s "q" | webget u -      Pipe: pass URL from search via stdin\n'
    "                                 (multi-line stdin = batch scrape)\n"
    "  webget login URL --profile X   Open browser, log in manually, persist session\n"
    "  webget profiles [--json]       List profiles and session status\n"
    "  webget logout URL --profile X  Clear auth for one domain, keep the rest\n"
    "  webget doctor [--json]         Show which browsers/tools are usable here\n"
    "  webget map URL [--limit N]     Discover URLs via sitemaps and robots.txt\n"
    "  webget crawl URL DB [--limit N] Bounded resumable crawl to SQLite\n"
    "Aliases: search = s, fetch = u, search-fetch = su\n"
    "\n"
    "Options:\n"
    "  -c, --cookies FILE  Netscape-format cookie file\n"
    "  --profile NAME      Persistent browser profile (auth session)\n"
    '  -H, --header "K: V" Extra header (repeatable)\n'
    "  -n, --max-chars N   Max output chars (default: 10000 for u, 4000 for su)\n"
    "  --limit N           Result count for s/su (default: 5 / 3)\n"
    "  -t, --timeout N     Per-URL timeout in seconds (default: 20)\n"
    "  -e, --engine NAMES  Search engines (ddgs metasearch): auto, or a\n"
    "                      comma-delimited subset like brave,duckduckgo.\n"
    "                      Unknown names degrade to auto. A dead engine\n"
    "                      falls over to others automatically (default: auto)\n"
    "  --json              Output results as JSON (with --engine provenance)\n"
    "  --fresh             Bypass cache and re-scrape\n"
    "  --ttl N             Cache TTL in seconds (default: 3600)\n"
    "  --strategy S        auto|http|crawl4ai|firecrawl (default auto)\n"
    "  --no-cache          Don't read or write the disk cache (private fetch)\n"
    "  --concurrency N     Max concurrent fetches for batch runs (default: 10)\n"
    "  --headless          Run login browser without a window\n"
    "\n"
    "Status values: success | login_required | challenge | blocked | error\n"
    "\n"
    "Session management:\n"
    "  webget login never stores passwords and never fills forms. You log in\n"
    "  yourself in the opened browser window; webget just persists the session.\n"
)


def parse_opts(args):
    """Extract options from positional args list.

    Numeric options are validated here, at parse time, because this is the
    only place with the user's actual input in hand. Previously only
    --concurrency was guarded (in main(), not here); the rest accepted
    impossible values and passed them into the system:

        -n -5            max_chars = -5  -> smart_truncate treats a
                         negative limit as "no limit" and returned 111
                         chars for 100 chars of text, and it created a
                         separate dead cache key.
        -t 0 / -t -1     timeout = 0 / -1, an httpx timeout that can
                         never succeed.
        --limit -1 / 0   zero or negative result count.
        --ttl -1         cache entry that is always expired.

    A syntax error (abc) already raised ValueError before, and that was
    correct; these guards add the semantic half, so a value that parses
    but cannot mean anything is rejected with the option name.
    """

    def _angka(flag, nilai, minimum=1, maksimum=None):
        """int(nilai) dengan pesan yang menyebut flag-nya.

        Batas atas opsional dipakai untuk max_chars, yang di MCP dibatasi
        _MAX_MAX_CHARS. Tanpa batas atas di CLI, input yang sama diterima
        di CLI tapi ditolak di MCP, sehingga agent yang belajar salah satu
        antarmuka terkejut di antarmuka lain.
        """
        # bool adalah subclass int di Python, jadi isinstance(True, int)
        # bernilai True dan True lolos sebagai 1. Tolak eksplisit supaya
        # input non-angka tidak diterima diam-diam. TypeError (bukan
        # ValueError) karena masalahnya adalah TIPE, bukan nilai yang di
        # luar rentang; main() menangkap keduanya dan mencetak pesan.
        if isinstance(nilai, bool):
            raise TypeError(f"{flag} expects a whole number, got {nilai!r}")
        try:
            n = int(nilai)
        except (TypeError, ValueError):
            raise ValueError(f"{flag} expects a whole number, got {nilai!r}") from None
        if n < minimum:
            raise ValueError(f"{flag} must be >= {minimum}, got {n}")
        if maksimum is not None and n > maksimum:
            raise ValueError(f"{flag} must be <= {maksimum}, got {n}")
        return n

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
    retry_transient = False
    engine = None
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
            concurrency = _angka("--concurrency", args[i + 1])
            i += 2
        elif args[i] in ("-r", "--retry", "--retry-transient"):
            retry_transient = True
            i += 1
        elif args[i] in ("-e", "--engine") and i + 1 < len(args):
            engine = args[i + 1]
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
            # Batas atas disamakan dengan MCP (_MAX_MAX_CHARS = 1_000_000).
            max_chars = _angka("--max-chars", args[i + 1], maksimum=1_000_000)
            i += 2
        elif args[i] == "--limit" and i + 1 < len(args):
            limit = _angka("--limit", args[i + 1])
            i += 2
        elif args[i] in ("-t", "--timeout") and i + 1 < len(args):
            timeout = _angka("--timeout", args[i + 1])
            i += 2
        elif args[i] == "--fresh":
            fresh = True
            i += 1
        elif args[i] == "--ttl" and i + 1 < len(args):
            ttl = _angka("--ttl", args[i + 1], minimum=0)
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
        retry_transient,
        engine,
    )


def _search_with_prov(query, n=5, engine=None):
    """Return (results, provenance) from the search layer.

    Calls the module-level name directly so linters see it as used. An
    earlier version used globals().get(), which made ruff strip the import
    as unused (F401) and silently killed real provenance; tests still
    passed because they monkeypatched the name back in.
    """
    return search_with_provenance(query, n=n, engine=engine)


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


def cmd_doctor(json_out=False):
    """Report the environment: which optional pieces are present and usable.

    Exists because "why did my crawl behave differently on this machine" is the
    most common question, and the answer is usually one of: a browser was
    detected but cannot be driven, crawl4ai is missing, or the search backend
    is unreachable. Printing the resolved decision beats guessing.
    """
    report: dict = {}

    # 1. Browser resolution for the crawl4ai pass.
    from . import browser as _browser

    try:
        choice = _browser.resolve_browser()
        browsers = _browser.scan_installed_browsers()
        report["browser"] = {
            "selected": choice.describe(),
            "source": choice.source,
            "usable": choice.usable,
            "browser_config_kwargs": choice.to_browser_config_kwargs(),
            "reason": choice.reason,
            "caveat": choice.caveat,
            "detected": [
                {"name": n, "channel": c, "path": p, "usable": bool(c)} for n, c, p in browsers
            ],
        }
    except ValueError as exc:
        report["browser"] = {"selected": None, "usable": False, "error": str(exc)}

    # 2. Optional crawl4ai dependency.
    try:
        import crawl4ai

        version = getattr(crawl4ai, "__version__", "unknown")
        if not isinstance(version, str):
            version = getattr(version, "__version__", "unknown")
        report["crawl4ai"] = {"installed": True, "version": version}
    except ImportError:
        report["crawl4ai"] = {
            "installed": False,
            "hint": "pip install webget-cli[browser] to enable the browser fallback",
        }

    # 3. Playwright's own download cache, if any.
    cache = os.path.expanduser("~/.cache/ms-playwright")
    bundled = []
    if os.path.isdir(cache):
        for entry in sorted(os.listdir(cache)):
            if entry.startswith("chromium-"):
                bundled.append(entry)
    report["playwright_cache"] = {"path": cache, "chromium_builds": bundled}

    # 4. Which optional pieces of the ladder are configured.
    report["env"] = {
        "WEBGET_BROWSER_CDP": os.environ.get("WEBGET_BROWSER_CDP") or None,
        "WEBGET_BROWSER_CHANNEL": os.environ.get("WEBGET_BROWSER_CHANNEL") or None,
        "WEBGET_BROWSER_PATH": os.environ.get("WEBGET_BROWSER_PATH") or None,
        "FIRECRAWL_API_KEY": "set" if os.environ.get("FIRECRAWL_API_KEY") else None,
    }

    if json_out:
        print(json.dumps(report, indent=2))
        return

    b = report["browser"]
    print("webget doctor")
    print("=" * 60)
    print("\nbrowser pass (crawl4ai):")
    if b.get("error"):
        print(f"  ERROR: {b['error']}")
    else:
        mark = "usable" if b["usable"] else "NOT USABLE"
        print(f"  selected: {b['selected']}  [{mark}]")
        print(f"  source:   {b['source']}")
        print(f"  reason:   {b['reason']}")
        if b["caveat"]:
            print(f"  caveat:   {b['caveat']}")
        kwargs = b["browser_config_kwargs"]
        print(f"  config:   {kwargs if kwargs else '{} (playwright bundled)'}")

    if b.get("detected"):
        print("\n  detected on this machine:")
        for d in b["detected"]:
            mark = "usable" if d["usable"] else "no playwright channel"
            print(f"    - {d['name']:<10} {d['path']}  ({mark})")
    else:
        print("\n  detected on this machine: none")

    c = report["crawl4ai"]
    print("\ncrawl4ai:")
    if c["installed"]:
        print(f"  installed, version {c['version']}")
    else:
        print(f"  NOT installed. {c['hint']}")

    pc = report["playwright_cache"]
    print("\nplaywright download cache:")
    print(f"  {pc['path']}")
    if pc["chromium_builds"]:
        for build in pc["chromium_builds"]:
            print(f"    - {build}")
    else:
        print("    (no chromium builds downloaded)")

    e = report["env"]
    print("\nenvironment overrides:")
    any_set = False
    for key, value in e.items():
        if value:
            print(f"  {key}={value}")
            any_set = True
    if not any_set:
        print("  (none set)")

    print("\n" + "=" * 60)


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return
    # `webget s --help` used to be treated as the query "help": --help was only
    # honoured in position 0, so there was no way to see usage for a
    # subcommand and a user asking for help got search results instead. Found
    # by scripts/artifact_smoke.py, not by the test suite.
    if (
        len(args) >= 2
        and args[0] in ("s", "search", "su", "search-using", "fetch", "f", "u")
        and args[1] in ("-h", "--help")
    ):
        print(__doc__)
        return

    # parse_opts raises ValueError for an unusable numeric option, and this
    # call used to sit outside any try, so the user saw a Python traceback:
    #
    #     Traceback (most recent call last):
    #       File ".../webget/cli.py", line 412, in main
    #         ) = parse_opts(args)
    #     ValueError: --max-chars expects a whole number, got 'abc'
    #
    # A CLI is a human interface; a traceback is not a message. Report the
    # option and exit 2 (the same code the now-redundant --concurrency
    # check used), so the failure reads like every other usage error here.
    try:
        (
            remaining,
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
            retry_transient,
            engine,
        ) = parse_opts(args)
    except (TypeError, ValueError) as exc:
        print(f"error: {exc}")
        sys.exit(2)

    if not remaining:
        print(__doc__)
        return

    if profile:
        try:
            os.makedirs(profile_dir(profile), exist_ok=True)
        except OSError as e:
            _warn(f"cannot create profile dir for '{profile}': {e}")

    # `remaining` adalah hasil parse_opts: argumen TANPA opsi, dalam urutan
    # asli. Sebelumnya main() memakai `args` MENTAH di sini, sehingga setiap
    # opsi yang muncul sebelum perintah atau query ikut terbaca sebagai
    # perintah/query:
    #
    #     webget --json u https://x/   -> args[0] = "--json" (bukan perintah)
    #     webget s --json uji-kata     -> args[2] = "uji-kata", lalu
    #                                     int("uji-kata") -> ValueError,
    #                                     jadi seluruh perintah gagal
    #
    # parse_opts sudah menghitung pemisahan itu dengan benar; hasilnya cuma
    # tidak pernah dipakai (RUF059). Sekarang dipakai.
    cmd = remaining[0]
    if cmd == "search":
        cmd = "s"
    elif cmd == "fetch":
        cmd = "u"
    elif cmd == "search-fetch":
        cmd = "su"
    q = remaining[1] if len(remaining) > 1 else ""

    def _jumlah_hasil(bawaan):
        """Jumlah hasil dari argumen ketiga (mis. 'webget s kata 10').

        Divalidasi di sini karena argumen posisional tidak melewati
        parse_opts, sehingga sebelum ini `webget s kata abc` membuang
        traceback:

            File ".../webget/cli.py", line 495, in main
              n = _jumlah_hasil(5)
            ValueError: invalid literal for int() with base 10: 'abc'

        Opsi --limit sudah divalidasi di parse_opts dan menang atas argumen
        posisional, jadi hanya dipakai kalau tidak ada --limit.
        """
        if limit:
            return limit
        if len(remaining) <= 2:
            return bawaan
        try:
            n = int(remaining[2])
        except (TypeError, ValueError):
            # ValueError naik ke pembungkus di main(); lihat komentar di sana
            # soal mengapa traceback tidak boleh sampai ke pengguna.
            raise ValueError(
                f"result count expects a whole number, got {remaining[2]!r}"
            ) from None
        if n < 1:
            raise ValueError(f"result count must be >= 1, got {n}")
        # Batas atas disamakan dengan MCP (_MAX_SEARCH_N = 50). Tanpa ini,
        # 'webget s kata 5000000' meminta lima juta hasil dari ddgs dan
        # menggantung, padahal MCP menolaknya.
        if n > 50:
            raise ValueError(f"result count must be <= 50, got {n}")
        return n

    try:
        _jalankan_perintah(
            cmd,
            q,
            remaining,
            _jumlah_hasil,
            cookies=cookies,
            headers=headers,
            profile=profile,
            headless=headless,
            json_out=json_out,
            engine=engine,
            limit=limit,
            strategy=strategy,
            ttl=ttl,
            fresh=fresh,
            no_cache=no_cache,
            concurrency=concurrency,
            retry_transient=retry_transient,
            max_chars_override=max_chars_override,
            timeout_override=timeout_override,
        )
    except (TypeError, ValueError) as exc:
        print(f"error: {exc}")
        sys.exit(2)


def _jalankan_perintah(
    cmd,
    q,
    remaining,
    _jumlah_hasil,
    *,
    cookies,
    headers,
    profile,
    headless,
    json_out,
    engine,
    limit,
    strategy,
    ttl,
    fresh,
    no_cache,
    concurrency,
    retry_transient,
    max_chars_override,
    timeout_override,
):
    """Dispatch satu perintah. Dipisah supaya main() bisa membungkusnya.

    Dipisah dari main() semata-mata agar ValueError dari validasi argumen
    posisional (_jumlah_hasil) bisa ditangkap di satu tempat dan dicetak
    sebagai pesan, bukan traceback. Tidak ada logika lain yang berubah.
    """
    if cmd == "login":
        sys.exit(cmd_login(q, profile, headless))
    elif cmd == "crawl":
        if len(remaining) < 3:
            raise ValueError("crawl expects URL and frontier SQLite path")
        result = asyncio.run(
            crawl_site(
                q,
                remaining[2],
                max_pages=limit or 100,
                timeout=timeout_override or 20,
                strategy=strategy,
            )
        )
        if json_out:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(f"done={result['stats'].get('done', 0)} failed={result['stats'].get('failed', 0)}")
        return
    elif cmd == "profiles":
        cmd_profiles(json_out)
        return
    elif cmd == "doctor":
        cmd_doctor(json_out)
        return
    elif cmd == "logout":
        sys.exit(cmd_logout(q, profile))
    elif cmd == "map":
        from .discovery import discover_urls

        # `limit` untuk map adalah batas JUMLAH URL, berbeda dari batas
        # jumlah hasil pencarian, jadi diterapkan di sini dan bukan di
        # parse_opts. Catatan: saat membersihkan sisa percobaan yang gagal,
        # gue sempat menulis `n = 100` dan menghapus dukungan --limit untuk
        # map; dikembalikan ke `limit or 100` supaya --limit bekerja lagi.
        n = limit or 100
        timeout = timeout_override or 10
        urls = asyncio.run(
            discover_urls(
                q,
                limit=n,
                timeout=timeout,
                headers=headers,
                allow_private=no_cache,
            )
        )
        if json_out:
            print(json.dumps(urls, indent=2))
            return
        for u in urls:
            print(u)
        return

    if cmd == "s":
        n = _jumlah_hasil(5)
        try:
            results, prov = _search_with_prov(q, n=n, engine=engine)
        except Exception as e:  # noqa: BLE001 - surface a clean error, not a traceback
            print(f"error: search failed: {e}")
            sys.exit(1)
        if json_out:
            # Provenance travels in the payload because the stderr warning
            # is lost the moment this is piped (webget s q --json | jq).
            print(
                json.dumps(
                    {
                        "results": results,
                        "engine": prov.get("engine"),
                        "requested_engine": prov.get("requested"),
                        "failed_over": prov.get("failed_over", False),
                    },
                    indent=2,
                )
            )
            return
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
                retry_transient=retry_transient,
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
        # Sama seperti cmd "s": ambil jumlah hasil dari remaining, bukan
        # args mentah, supaya opsi tidak bocor menjadi nilai int().
        n = _jumlah_hasil(3)
        max_chars = max_chars_override or 4000
        timeout = timeout_override or 20
        try:
            results = search(q, n=n, engine=engine)
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
                    "metadata": got.get("metadata"),
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
            # markdown is already smart-truncated (and marked) by the fetchers;
            # a second [:max_chars] here would chop the truncation marker off.
            print(got.get("markdown", "") if not fail else "(no content)")

    else:
        print("Unknown command:", cmd)


if __name__ == "__main__":
    main()
