<div align="center">

# webget

**Local search + scrape CLI. Zero API keys, unlimited usage.**

`webget` is a web acquisition layer for agents and scripts: it routes every URL
through a strategy ladder (HTTP fast path → Crawl4AI browser → optional
Firecrawl), and reports *provenance* - where the content came from and whether
the session that fetched it can be trusted.

[![CI](https://img.shields.io/github/actions/workflow/status/DavidPandleton/webget/ci.yml?label=CI&logo=github)](https://github.com/DavidPandleton/webget/actions)
[![PyPI](https://img.shields.io/pypi/v/webget-cli.svg)](https://pypi.org/project/webget-cli/)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Stars](https://img.shields.io/github/stars/DavidPandleton/webget?style=social)](https://github.com/DavidPandleton/webget)

</div>

---

## Why

Most scraping tools assume one engine. webget assumes the web is messy:

```text
FETCH_AUTO
├── http       → fast path (httpx + trafilatura/html2text), no browser
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

Crawl4AI drives a Playwright Chromium. `pip install "webget-cli[browser]"`
installs the Python packages; the browser binary itself is downloaded
separately:

```bash
python -m playwright install chromium
```

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
webget s "rust async runtime"             # search DuckDuckGo (top 5)
webget u https://example.com              # scrape (auto: http -> crawl4ai)
webget su "llm inference" 5               # search + scrape top 5, parallel
cat urls.txt | webget u -                 # batch scrape, one browser instance
webget fetch https://example.com --json   # machine-readable result
```

Long aliases: `search` = `s`, `fetch` = `u`, `search-fetch` = `su`.

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
    "error": null
  }
}
```

Status values: `success | login_required | challenge | blocked | error`.

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

> **Limitation:** MCP tools do not expose `--profile`/`--cookies`, so
> authenticated pages are out of scope for the MCP server. Use the CLI
> (`webget login`, `webget u --profile ...`) for session-based fetching.

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

[MIT](LICENSE)
