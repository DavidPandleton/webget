<div align="center">

# webget

**Local search + scrape CLI. Zero API keys, unlimited usage.**

`webget` is a web acquisition layer for agents and scripts: it routes every URL
through a strategy ladder (HTTP fast path → Crawl4AI browser → optional
Firecrawl), and reports *provenance* - where the content came from and whether
the session that fetched it can be trusted.

[![CI](https://github.com/DavidPandleton/webget/actions/workflows/ci.yml/badge.svg)](https://github.com/DavidPandleton/webget/actions)
[![PyPI](https://img.shields.io/pypi/v/webget-cli.svg?cacheSeconds=3600)](https://pypi.org/project/webget-cli/)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg?cacheSeconds=3600)](LICENSE)
[![Stars](https://img.shields.io/github/stars/DavidPandleton/webget?style=social)](https://github.com/DavidPandleton/webget)

</div>

---

## Why

Most scraping tools assume one engine. webget assumes the web is messy:

```text
FETCH_AUTO
├── http       → fast path (httpx + trafilatura/markdownify), no browser
├── crawl4ai   → Playwright browser, JS rendering, persistent auth sessions
└── firecrawl  → optional cloud fallback (needs WEBGET_FIRECRAWL_KEY)
```

Every fetch classifies what it hit - `success`, `login_required`, `challenge`,
`blocked`, or `error` - and reports it in machine-readable JSON. webget never
pretends an empty page is success, and it never solves CAPTCHAs or evades
anti-bot systems; it tells you honestly what happened.

## Install

Requires Python 3.11+.

### PyPI (`webget-cli`)

The CLI command is `webget`; the PyPI package name is `webget-cli`
(the bare `webget` name is taken by an unrelated package).

```bash
# pip - HTTP fast path + search only (no browser)
pip install webget-cli

# pip - full stack with Crawl4AI/Playwright browser fallback
pip install "webget-cli[browser]"

# uv tool - isolated executable on your PATH
# (single spec with extras: do NOT use `--with "webget-cli[browser]"`,
#  uv re-resolves the same package there and can clobber the main install
#  with a stale version)
uv tool install "webget-cli[browser,mcp]"
```

After install, `webget` is available as a command:

```bash
webget --help
```

### Browser runtime (optional)

Crawl4AI drives a Playwright browser. `pip install "webget-cli[browser]"`
installs the Python packages; the browser binary itself is downloaded
separately:

```bash
python -m playwright install chromium
```

**You may not need that download.** Before reaching for Playwright's bundled
Chromium, webget looks for a Chromium-family browser already installed on the
machine and uses it when it can actually be driven. Run `webget doctor` to see
what was found and what will be used:

```bash
webget doctor
```

Resolution order (first match wins):

| Step | Source | Notes |
|---|---|---|
| 1 | `WEBGET_BROWSER_CDP` | Attach to a running browser over CDP. The only route that works for browsers with no Playwright channel. |
| 2 | `WEBGET_BROWSER_CHANNEL` | A Playwright channel: `chrome`, `msedge`, or `chromium`. |
| 3 | `WEBGET_BROWSER_PATH` | An explicit binary. Accepted, but see the limitation below. |
| 4 | Auto-detected browser | Chrome, Edge, Chromium, Brave, Vivaldi or Opera found on the machine. |
| 5 | Playwright bundled Chromium | The fallback when nothing usable is found. |

Two limitations worth knowing, both reported honestly by `webget doctor`
instead of failing quietly:

- **Brave, Vivaldi and Opera cannot be launched by crawl4ai.** Playwright has no
  channel for them, and crawl4ai 0.9.2 does not forward `executable_path` to
  Playwright, so there is no path to the binary. They are detected and reported
  as NOT USABLE. The workaround is CDP: start the browser with
  `--remote-debugging-port=9222`, then set
  `WEBGET_BROWSER_CDP=http://127.0.0.1:9222`.
- **CDP is opt-in on purpose.** Attaching to a browser you are already logged
  into mixes your personal session cookies into crawl output, so webget never
  auto-detects an open debugging port.

System Firefox is never selected: Playwright drives Firefox over WebDriver BiDi
with its own patched build.

Without the browser extra, `webget` still works for search and plain HTTP
fetches. A fetch that needs the browser (JS rendering, `--profile` sessions,
`login`) prints a clear warning telling you how to install it.

### From source (development)

```bash
git clone https://github.com/DavidPandleton/webget
cd webget
uv pip install -e ".[dev,browser]"
```

## Usage

```bash
webget s "rust async runtime"             # search (ddgs metasearch, top 5)
webget s "rust async runtime" --engine brave,duckduckgo   # subset: faster than auto
webget s "rust async runtime" --json      # machine-readable, includes engine provenance
webget u https://example.com              # scrape (auto: http -> crawl4ai)
webget su "llm inference" 5               # search + scrape top 5, parallel
cat urls.txt | webget u -                 # batch scrape, one browser instance
webget crawl https://example.com crawl.db --limit 20 --json  # resumable local crawl
webget fetch https://example.com --json   # machine-readable result
```

Long aliases: `search` = `s`, `fetch` = `u`, `search-fetch` = `su`.

### Resumable local crawl

The crawler is local and bounded: it uses SQLite for durable frontier state
and page results, stays on the seed hostname, and can resume after a process
restart. It does not require Docker, Redis, Postgres, or a browser.

```bash
webget crawl https://example.com ./crawl.db --limit 20 --timeout 20 --json
```

The same operation is available through Python as `webget.crawl_site(...)` and
through MCP as the `crawl` tool. Results can also be exported with the Python
API to JSONL or Markdown. Stale in-progress leases are recovered using a
bounded timeout.

## Search engines

webget searches through `ddgs`, a metasearch library that aggregates several
keyless engines. The default `auto` queries all of them; naming a subset is
faster and skips engines that are having a bad hour.

```bash
webget s "query" --engine brave,duckduckgo
```

**Do not hardcode engine names.** The set ddgs offers changes between its
releases, and webget validates against the registry at runtime rather than
a built-in list. Pass a deliberately bogus name to see what your installed
version supports:

```bash
$ webget s "query" --engine bogus
warning: unknown search engine(s): bogus - using auto
  (known: brave, duckduckgo, google, grokipedia, mojeek, startpage, wikipedia, yahoo)
```

Unknown names degrade to `auto` with a warning instead of failing, so a
typo never kills a search.

### Optional SearXNG provider

SearXNG is not bundled or required. Point the optional HTTP adapter at an
existing instance using an environment variable:

```bash
export WEBGET_SEARXNG_URL=http://localhost:8080
```

Then use the provider from Python:

```python
from webget import SearxngSearchProvider
from webget.search import search_with_provenance

provider = SearxngSearchProvider()
results, provenance = search_with_provenance(
    "query", n=5, engine="searxng", provider=provider
)
```

The adapter calls `/search?format=json`, normalizes result fields, and keeps
the usual per-call provenance. Live availability depends on the configured
SearXNG instance; the core package does not start one.

**Failover is automatic.** If the engine you named fails, or returns zero
results, webget tries the remaining engines until a time budget is spent
(15s by default, `WEBGET_FAILOVER_BUDGET_S` to override) and returns the
first non-empty set. The budget replaced a fixed "3 engines" cap: a count is
either too many (3 x 20s timeout = a minute of dead air) or too few (a live
engine sitting fifth in line never got reached). At least two alternates are
always tried, however fast the budget expires. This is not paranoia: engine
reachability depends on where you are, not just whether a service is up. A
benchmark from one residential connection found only 2 of 9 engines
reachable, and `auto` survived purely because they did.

Every substitution is announced, never silent:

```bash
$ webget s "rust programming language book" --engine google
warning: engine 'google' failed (No results found.); fell back to 'brave'
1. The Rust Programming Language - doc.rust-lang.org/book/
```

**Results carry provenance.** Because failover can answer from an engine you
did not ask for, `--json` reports which one actually did:

```bash
$ webget s "linux kernel" --engine google --json
{
  "results": [ ... ],
  "engine": "grokipedia",         # who actually answered
  "requested_engine": "google",
  "failed_over": true
}
```

This matters most over MCP, where a stderr warning is invisible to the
agent: the payload is the only signal that the results came from elsewhere.

**Failover order is learned, not hardcoded.** webget keeps a small health
ledger per install (`~/.local/state/webget/engine_health.json`) recording
whether each engine answered and how fast. When failover kicks in, engines
are tried best-first by that record instead of in registry order. The ledger
is advisory - it reorders candidates, it never removes one - and it decays:
an engine marked dead last week is retried with no penalty this week, because
the blocking that made it dead is exactly what changes. There is no baked-in
ranking anywhere in the source; the same webget learns a different order on
your machine than on the author's, which is the point.

Diagnostics: `python -c "from webget import health; print(health.health_snapshot())"`

> **MCP note (breaking in 0.13.0):** the `search` and `search_fetch` tools
> now return an object `{results, engine, requested_engine, failed_over}`
> instead of a bare list, so provenance has somewhere to live. Errors are
> `{error, results: []}`.

Provenance is per-call, not per-result: `ddgs` merges every engine's hits
into one list and discards which engine supplied each hit, so per-hit
attribution is not obtainable through its public API.

### Options

| Flag | Meaning |
|---|---|
| `-c, --cookies FILE` | Netscape-format cookie file |
| `--profile NAME` | Persistent browser profile (auth session) |
| `-H, --header "K: V"` | Extra header (repeatable) |
| `-n, --max-chars N` | Max output chars (default: 10000 for `u`, 4000 for `su`) |
| `--limit N` | Result count for `s`/`su` |
| `-t, --timeout N` | Per-URL timeout seconds (default 20) |
| `--fresh` | Bypass cache |
| `--ttl N` | Cache TTL seconds (default 3600) |
| `--strategy S` | `auto` \| `http` \| `crawl4ai` \| `firecrawl` |
| `--no-cache` | Don't read or write the disk cache (private fetch) |
| `--json` | JSON output with metadata |

## Authenticated sessions (profiles)

```bash
# interactive login: browser opens, YOU log in manually, session persists
webget login https://campus.example --profile campus

# list profiles and their session status
webget profiles
webget profiles --json

# later fetches reuse the session - even on the HTTP fast path
webget fetch https://campus.example/dashboard --profile campus --json

# log out ONE domain, keep the rest of the profile
webget logout https://campus.example --profile campus
```

`webget login` never stores passwords and never fills forms. A visible
browser opens, you authenticate yourself, then press Enter in the terminal and
webget persists the session. Persistent profiles live in
`~/.local/share/webget/profiles/<name>`; session cookies are exported to
`storage_state.json` inside the profile after each browser run, so the fast
path can reuse them. Secrets are never printed.

## JSON output

`--json` returns a dict keyed by URL, so batch results are easy to inspect:

```json
{
  "https://campus.example/dashboard": {
    "status": "success",
    "method": "crawl4ai",
    "cached": false,
    "attempts": 1,
    "auth": {
      "profile": "campus",
      "authenticated": true,
      "state": "success"
    },
    "metadata": {
      "author": "Jane Doe",
      "published_at": "2026-09-01",
      "site_name": "Campus Portal",
      "language": "id"
    },
    "error": null
  }
}
```

`metadata` (author, published date, site name, language) comes from
trafilatura extraction on the HTTP path; values are `null` when unknown
or when the winning strategy was not HTTP.

Status values: `success | login_required | challenge | blocked | error`.

The HTTP path also handles non-HTML: JSON becomes a pretty code block,
CSV becomes a GFM table, RSS/Atom feeds become a link list, PDFs are
extracted per page (via `pypdf`), plain text passes through. Valid
non-HTML payloads count as success even when short.

## Status detection rules

| Signal | State |
|---|---|
| Valid content (≥100 chars) | `success` |
| HTTP 401, login form, 403 + login markers | `login_required` |
| Cloudflare / CAPTCHA / "verify you are human" | `challenge` |
| HTTP 403 generic, 429, "access denied" | `blocked` |
| DNS failure, timeout, unexpected exception | `error` |

## Cache

Results are cached in `~/.cache/webget/` (sha1 of url + profile + options,
TTL 1h, eviction at 500 files). The cache is **content-level, not
strategy-level**, and **isolated per profile** - public, `campus`, and `work`
fetches never collide. Failures are never cached.

> **Privacy note:** cached content is plaintext JSON on disk. If you fetch
> authenticated/personal pages, use `--no-cache`.

## MCP server

`webget_mcp.py` exposes the same ladder as an MCP server (`search`,
`fetch`, `search_fetch`), so agents like opencode can search and scrape
without API keys:

```bash
pip install "webget-cli[mcp]"
# or as a uv tool with the MCP server + browser fallback:
uv tool install "webget-cli[browser,mcp]"
```

Register as a local MCP server in opencode:

```jsonc
{
  "mcp": {
    "webget": {
      "type": "local",
      "command": ["webget-mcp"],
      "enabled": true
    }
  }
}
```

Then prompt with `use webget` for search and scrape tasks. Run the server
standalone with `webget-mcp` (stdio transport) or `python webget_mcp.py`.

### Breaking change in 0.13.0: search returns an object, not a list

`search` and `search_fetch` used to return a bare JSON array of results.
They now return an object, because a list has nowhere to carry provenance.
The MCP result-count argument is `limit` (default 5 for `search`, 3 for
`search_fetch`), matching the CLI vocabulary. The legacy MCP argument `n`
remains accepted as an explicit compatibility alias; when both are supplied,
`n` wins. The Python API continues to use `n` for backward compatibility.

```jsonc
// <= 0.12.1  ->  a list
[ { "title": "...", "href": "...", "body": "..." }, ... ]

// >= 0.13.0  ->  an object
{
  "results": [ { "title": "...", "href": "...", "body": "..." }, ... ],
  "engine": "brave",            // the engine that actually answered
  "requested_engine": "google", // what was asked for
  "failed_over": true           // an alternative was attempted
}
```

Error responses changed shape too:

```jsonc
// <= 0.12.1  ->  a plain error string / empty list
// >= 0.13.0
{
  "error": "SearchError: RequestError(...)",
  "results": []
}
```

**How to migrate.** If you read the tool result as an array, read
`.results` instead. In JavaScript that is `result.results` rather than
`result`; in Python `data["results"]` rather than `data`. If you only
iterate the hits, the change is mechanical.

**Why it had to break.** Failover means the engine that answers is not
always the engine that was asked for, and over MCP there is no stderr for a
warning to land on, so the payload is the only place that fact can live.
Keeping the list shape would have meant silently returning results from an
unexpected engine with no way for a client to notice. An additive field was
not possible: a JSON array cannot carry sibling keys.

`fetch` is unchanged and still returns its string payload. The Python API is
unchanged: `search()` still returns a list, and `search_with_provenance()`
is the new opt-in that returns `(results, provenance)`.

### Authenticated sessions (profiles)

MCP tools can use locally stored login sessions. Create one first with the
CLI, or let the agent create it via the MCP `login` tool:

```bash
webget login https://portal.example.com --profile portal
```

Then the agent can discover sessions and fetch authenticated pages:

- `list_profiles` - lists available sessions (name, last used, size,
  status). Cookie values are never returned.
- `login(url, profile)` - opens a browser session (headful by default so a
  human can log in), navigates to `url`, and persists the session once the
  login handshake's cookies appear (or after `wait_seconds`, whichever
  comes first). MCP stdin is the JSON-RPC stream, so there is no Enter
  keypress; the flow polls for cookies instead.
- `fetch(..., profile="portal")` / `search_fetch(..., profile="portal")` -
  scrape using that session.

```text
agent: "check my portal for new announcements"
  1. list_profiles            -> portal (authenticated)
  2. login(https://portal.example.com, profile="portal")  # if not listed
  3. fetch(url, profile="portal")
```

Invalid profile names and unknown profiles are hard errors (no silent
anonymous fallback). Sessions are local to the machine running the MCP
server.

## Development

```bash
make dev        # install runtime + dev deps
make test       # pytest (pure logic, no network needed)
make lint       # ruff
```

- Single-file Python (`webget_cli.py`), no build step, runs via `uv run`.
- Lazy imports: `--strategy http` never pays the Crawl4AI import cost.
- Crawl4AI 0.9.2's `export_storage_state()` is broken (wrong attribute);
  webget works around it by reaching into `browser_manager` directly.

## Contributing

Found a bug or have an idea? [Open an issue](https://github.com/DavidPandleton/webget/issues/new/choose) - we have templates. Pull requests welcome, see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[Apache-2.0](LICENSE)

Note: dependency licenses are separate (e.g. certifi MPL-2.0, tqdm
MPL-2.0 AND MIT, scipy's bundled GCC-runtime GPL-with-exception via the
browser extra); each dependency keeps its own license/notice.
