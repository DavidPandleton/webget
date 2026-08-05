# webget

Local search + scrape CLI. Zero API keys, unlimited usage.

```
webget s "query" [n]           Search via DuckDuckGo (default 5)
webget u "https://..."         Scrape URL -> markdown (HTTP fast path, falls back)
webget su "query" [n]          Search + scrape top n results (default 3, parallel)
webget s "q" | webget u -      Pipe: pass URL from search via stdin (batch)
```

Aliases: `search` = `s`, `fetch` = `u`, `search-fetch` = `su`.

## Why

A web acquisition layer for agents. Instead of one scraping engine, webget routes
each URL through a strategy ladder and reports *provenance* — where the content
came from and whether it's trustworthy:

```
FETCH_AUTO
├── http       → fast path (httpx + trafilatura/html2text), no browser
├── crawl4ai   → Playwright browser, JS rendering
└── firecrawl  → optional cloud fallback (needs WEBGET_FIRECRAWL_KEY)
```

## Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv pip install --python $(which python3) \
  crawl4ai ddgs httpx trafilatura html2text
cp webget ~/.local/bin/webget
chmod +x ~/.local/bin/webget
```

## Usage

```bash
webget s "rust async runtime"            # search
webget u https://example.com             # scrape (auto: http -> crawl4ai)
webget su "llm inference" 5              # search + scrape top 5
cat urls.txt | webget u -                # batch scrape
webget fetch https://example.com --json  # machine-readable
```

### Options

| Flag | Meaning |
|---|---|
| `-c, --cookies FILE` | Netscape-format cookie file |
| `--profile NAME` | Persistent browser profile (auth session) |
| `-H, --header "K: V"` | Extra header (repeatable) |
| `-n, --max-chars N` | Max output chars |
| `--limit N` | Result count for `s`/`su` |
| `-t, --timeout N` | Per-URL timeout (default 20) |
| `--fresh` | Bypass cache |
| `--ttl N` | Cache TTL seconds (default 3600) |
| `--strategy S` | `auto` \| `http` \| `crawl4ai` \| `firecrawl` |
| `--json` | JSON output with metadata |

### Authenticated sessions (profiles)

```bash
# export cookies once, then reuse the session
webget fetch https://campus.example/dashboard --cookies ~/cookies.txt --profile campus
webget fetch https://campus.example/dashboard --profile campus --json
```

Persistent profiles live in `~/.local/share/webget/profiles/<name>`.
Cookies/tokens are never printed or stored in the repo.

### Status values

`success | login_required | challenge | blocked | error`

Auth-state is detected from observable signals (HTTP status, login forms,
Cloudflare/CAPTCHA markers). webget does **not** solve CAPTCHAs, spoof
fingerprints, or bypass anti-bot systems — it just tells you honestly what
it hit.

## Cache

Results cached in `~/.cache/webget/` (sha1 key of url + profile + options,
TTL 1h, eviction cap 500 files). Cache is content-level, not strategy-level,
and isolated per profile.

## Design notes

- Single-file Python, no build step, runs via `uv run`
- Lazy imports: `--strategy http` never pays the Crawl4AI import cost
- Failures are never cached; only successful acquisitions persist
- JSON output includes `status`, `method`, `cached`, `attempts`, `auth`
  so consumers know data provenance

## License

MIT
