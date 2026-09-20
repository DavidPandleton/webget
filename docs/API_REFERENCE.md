# webget API Reference

Reference for the `webget` command-line tool and the two modules that back
it: `webget/cli.py` (option parsing + command dispatch) and `webget/ladder.py`
(the HTTP → Crawl4AI → Firecrawl scrape ladder).

The document has three sections:

- [CLI reference](#cli-reference)  -  every subcommand and flag, with examples.
- [`webget.cli` public functions](#webgetclipl)
- [`webget.ladder` public functions](#webgetladderpl)

Everything is read-only documentation of the public surface; internal
(underscore-prefixed) helpers are listed for completeness in each Module
section so readers can spot them, but only non-underscore functions are
"public".

---

## CLI reference

`webget` is a single binary with one "engine" (the scrape ladder) and several
subcommands. Enter `webget --help` for this text at runtime.

### Subcommands

| Subcommand | Alias | Purpose |
|---|---|---|
| `s "<query>" [n]` | `search` | Search the web via the DDGS metasearch (default 5 results). |
| `u "<url>"` | `fetch` | Scrape a URL to markdown (HTTP fast path, falls back up the ladder). |
| `su "<query>" [n]` | `search-fetch` | Search, then scrape the top `n` results in parallel (default 3). |
| `login <url> --profile <name>` |  -  | Open a browser, let you log in manually, persist the session. |
| `profiles` |  -  | List profiles and their auth/session status. |
| `logout <url> --profile <name>` |  -  | Clear auth for one domain in a profile, keep the rest. |
| `map <url>` |  -  | Discover URLs via sitemaps and `robots.txt`. |

Example usage:

```sh
# Search the web, print top 5 results
webget s "python async"

# Scrape a single URL to markdown
webget u "https://docs.python.org/3/library/asyncio.html"

# Search + scrape the top 3 results (default) in parallel
webget su "fastapi tutorial"

# Pipe: pass the URL from a search straight to a fetch
webget s "rust" | webget u -

# Search a specific count
webget s "kubernetes" 20

# Manage authenticated sessions per domain
webget login "https://github.com" --profile work
webget profiles
webget profiles --json
webget logout "https://github.com" --profile work

# Discover URLs from sitemaps / robots.txt
webget map "https://example.com"

# Batch scrape from a multi-line stdin stream
printf 'https://a.io/page1\nhttps://a.io/page2\n' | webget u -
```

**Status values** reported per fetch: `success | login_required | challenge |
blocked | error`.

### Global options (flags)

Options may be placed anywhere on the command line and apply to whichever
subcommand runs.

| Flag(s) | Argument | Default | Description |
|---|---|---|---|
| `-c`, `--cookies` | FILE |  -  | Read a Netscape-format cookie file and send those cookies. |
| `--profile` | NAME |  -  | Use a named persistent browser profile (auth session). |
| `-H`, `--header` | `"K: V"` |  -  | Add an extra HTTP header. Repeatable. |
| `-n`, `--max-chars` | N | `10000` for `u`, `4000` for `su` | Cap output characters per fetched page. |
| `--limit` | N | `5` for `s`, `3` for `su` | Result count for `s` / `su`. |
| `-t`, `--timeout` | N | `20` | Per-URL timeout in seconds. |
| `-e`, `--engine` | NAMES | `auto` | Search engine(s) to use (`auto`, or a comma-delimited subset like `brave,duckduckgo`). Unknown names degrade to `auto`; a dead engine falls over to others automatically. |
| `--json` |  -  | `false` | Emit results as JSON (includes `--engine` provenance for `s`). |
| `--fresh` |  -  | `false` | Bypass the cache and re-scrape. |
| `--ttl` | N | `3600` | Cache TTL in seconds. |
| `--strategy` | S | `auto` | Scrape strategy: `auto`, `http`, `crawl4ai`, or `firecrawl`. |
| `--no-cache` |  -  | `false` | Don't read or write the disk cache (private fetch). |
| `--concurrency` | N | `10` | Max concurrent fetches for batch runs. |
| `-r`, `--retry`, `--retry-transient` |  -  | `false` | Retry transient timeouts once on the fast HTTP path (with a longer timeout). |
| `--headless` |  -  | `false` | Run the login browser without a window. |
| `-h`, `--help` |  -  |  -  | Print the full usage/help text. Accepted at position 0 and as a subcommand's first argument. |

Example usage:

```sh
# Cookies + extra headers + JSON output
webget u "https://api.example.com/x" -c cookies.txt -H "Authorization: Bearer x" --json

# Fetch deeply into a page's content
webget u "https://example.com/" -n 50000

# Give search+fetch a higher result count and shorter timeout
webget su "node express" --limit 5 -t 10

# Cap parallel fetches for a 100-URL batch
printf 'https://s.io/%s\n' {1..100} | webget u - --concurrency 20

# Force the browser strategy (bypasses the HTTP fast path)
webget u "https://js-heavy.example/" --strategy crawl4ai

# Private fetch that touches neither read nor write path of the cache
webget u "https://example.com/" --no-cache

# Parse the CLI options programmatically (see webget.cli.parse_opts)
```

---

## `webget.cli`

Module-level `__doc__` is the user-facing help text and is what `--help`
prints; the flags it documents are the source of truth for the CLI table
above. The four non-underscore functions below make up the public API.

### `parse_opts(args)`

**1 line:** Extract and validate options from a positional-args list, returning a
15-element tuple of normalized values plus the remaining positional args.

**Signature:** `parse_opts(args) -> tuple`

Returns, in order: `(remaining, cookies, headers, max_chars, timeout, fresh,
ttl, json_out, limit, strategy, profile, no_cache, headless, concurrency,
retry_transient, engine)`. The same options the CLI table describes; unknown
flags are passed through in `remaining`. `cookies` is the parsed cookie dict,
`headers` the parsed header list (from `-H`/`--header`).

**Example:**

```python
from webget.cli import parse_opts

args, cookies, headers, max_chars, timeout, fresh, ttl, json_out, \
    limit, strategy, profile, no_cache, headless, concurrency, \
    retry_transient, engine = parse_opts(
        ["s", "asyncio", "--limit", "10", "--json"]
    )
# args == ["s", "asyncio"], limit == 10, json_out == True
```

### `cmd_profiles(json_out)`

**1 line:** Print the list of profiles (name, last used, size in MB, status), as
a human table or as JSON.

**Signature:** `cmd_profiles(json_out: bool) -> None`

With `json_out=True` prints a JSON object keyed by profile name; otherwise a
fixed-width text table. Prints `(no profiles yet)` / `{}` when no profile
directory exists.

**Example:**

```python
cmd_profiles(json_out=True)
# {"work": {"profile": "work", "last_used": ..., "size": ..., "status": ...}}
```

### `cmd_login(site, profile, headless)`

**1 line:** Validate a site URL and profile, then open the browser so the user
can log in manually and persist the session.

**Signature:** `cmd_login(site: str, profile: str, headless: bool) -> int`

Returns `2` on invalid site URL or missing/invalid profile, `0` on success. Does
not fill forms or store passwords.

**Example:**

```python
code = cmd_login("https://github.com", "work", headless=False)  # -> 0
```

### `cmd_logout(site, profile)`

**1 line:** Clear the persisted auth for one domain in a profile, leaving every
other domain in that profile untouched.

**Signature:** `cmd_logout(site: str, profile: str) -> int`

Returns `2` on missing/invalid profile or site URL, `1` if the domain had no
auth to clear, `0` on success.

**Example:**

```python
code = cmd_logout("https://github.com", "work")  # -> 0
```

### `main()`

**1 line:** Entry point  -  reads `sys.argv`, parses options, and dispatches to the
matching subcommand.

**Signature:** `main() -> None`

Handles `-h`/`--help` at position 0 and as a subcommand's first argument,
prints usage when no command is given, enforces `--concurrency >= 1`, and
dispatches `login`, `profiles`, `logout`, `map`, `s`, `u`, and `su`. Exits with
non-zero codes on errors.

**Example:**

```python
if __name__ == "__main__":
    from webget.cli import main
    main()
```

---

## `webget.ladder`

The scrape ladder orchestrates three strategies **in series** per URL:
`http` → `crawl4ai` (browser) → `firecrawl` (cloud). Each step is tried in
order; success promotes to cache and returns, failure is recorded with reasons
and the ladder escalates. Per-domain strategy memory promotes a known-good
strategy to the front of subsequent `auto` runs.

**Public function:** `scrape_many`. The remaining callables in the module are
internal helpers (underscore-prefixed) and are listed for reference.

### `scrape_many(urls, ...)`

**1 line:** Scrape a list of URLs through the strategy ladder, returning a
dict keyed by URL of per-URL result objects.

**Signature:**
```python
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
)
```

**Behavior:**

1. Builds the ladder steps from `strategy` (auto includes `http`, `crawl4ai`,
   and `firecrawl` when a Firecrawl key is configured). When `strategy="auto"`
   and strategy-memory exists for the batch's majority domain, the known-good
   strategy is moved to the front.
2. Dedupes URLs, then runs a concurrent **SSRF guard** over every URL (each
   target's DNS is resolved and checked for private/reserved addresses) before
   any strategy sees it. Blocked targets get a `status: "error"` result with
   `attempts: 0`.
3. Consults the cache first (unless `no_cache`); hits are normalized to the
   result shape with `method: "cache"`, `cached: True`.
4. Runs the ladder passes (`http` incl. optional transient retry, then
   `crawl4ai`, then `firecrawl`), respecting `max_concurrency` (default `10`).
5. URLs that exhaust the ladder get a terminal state from recorded reasons in
   priority order: `challenge > login_required > blocked > error`.

Each result dict has the shape:
`title, markdown, metadata, status, method, cached, attempts, error, auth,
reasons`. `reasons` is always a list (empty on success).

**Example:**

```python
import asyncio
from webget.ladder import scrape_many

results = asyncio.run(
    scrape_many(
        ["https://docs.python.org/3/", "https://realpython.com/"],
        max_chars=8000,
        per_url_timeout=30,
        max_concurrency=2,
    )
)
for url, r in results.items():
    print(url, r["status"], r["method"], len(r["markdown"]))
```

---

### Internal helpers (`webget.ladder`, for reference)

These prefixed callables are module-internal  -  none are part of the public API,
but they are documented here so the module is fully legible.

| Function | 1-line description |
|---|---|
| `_warn(msg)` | Print `webget: warning: <msg>` to stderr so stdout (JSON) stays clean. |
| `_ladder(strategy, key)` | Map a strategy name to its ordered list of ladder steps (`http`/`crawl4ai`/`firecrawl`), raising `SystemExit` for unknown strategies or a missing Firecrawl key. |
| `_strategy_memory_path()` | Return the filesystem path to the per-domain strategy-memory JSON. |
| `_load_strategy_memory()` | Load and normalize strategy memory to `{domain: {"method", "ts"}}`, dropping entries older than the TTL (14 days). |
| `_save_strategy_memory(memory)` | Atomically write strategy memory (`mkstemp` + `os.replace`); best-effort, ignores `OSError`. |
| `_learn_strategy(domain, method)` | Record that `method` succeeded for `domain` so future `auto` runs try it first; only writes when the value changes. |
| `_reorder_steps_by_domain(steps, url)` | When `strategy="auto"` and memory names a preferred method for the URL's domain, move that step to the front of the ladder. |
| `_empty_meta()` | Return a fresh, empty metadata dict (never shared mutable module state). |
| `_normalize_hit(hit)` | Turn a cache record into the standard per-URL result dict (`method: "cache"`, `cached: True`). |
| `_crawl4ai_once(crawler, cfg, url, per_url_timeout)` | Run one Crawl4AI crawl with a single retry on transient failures (never on auth/403/challenge), returning title/markdown/status/html. |
| `_resolve_crawl4ai_once()` | Return the crawl4ai-once implementation, resolving the shim-module patch so tests can substitute a fake. |
| `_resolve_fetch_http()` | Return the HTTP fetcher, honoring shim-module test patches. |
| `_resolve_is_private_target()` | Return the SSRF address-policy function, honoring shim-module test patches. |
| `_resolve_private_ip_for()` | Return the private-IP detail function, honoring shim-module test patches. |
| `_terminal_state(reasons, profile)` | Pick the final result state from ladder reasons, with priority `challenge > login_required > blocked > error`. |
| `_auth_message(state, profile)` | Return a human explanation string for a terminal auth state (`login_required`, `challenge`, `blocked`). |

---

## Related environment / config notes

- `WEBGET_FIRECRAWL_KEY`  -  required to use the `firecrawl` strategy pass.
- `WEBGET_ALLOW_PRIVATE=1`  -  escape hatch (from the SSRF audit) that permits
  deliberate intranet/private-target scraper use.

For the deeper architecture, SSRF guards, concurrency bounds, and caching
details, see [`docs/deep-audit-2026-08-06.md`](deep-audit-2026-08-06.md).