# Changelog

All notable changes to webget are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/) and [SemVer](https://semver.org/).

## [Unreleased]

## [0.8.0] - 2026-08-08

### Added
- MCP server: `list_profiles` tool (non-sensitive session metadata) and a
  `profile` parameter on `fetch` / `search_fetch` for authenticated
  scraping with locally stored sessions (`webget login`). Cookie values
  are never exposed; invalid or unknown profile names return clean errors
  instead of a silent anonymous fallback.
- `WEBGET_PROFILE_DIR` env override for the profile root (tests/ops).

## [0.7.3] - 2026-08-08

### Changed
- HTML-to-Markdown fallback from html2text to markdownify.
- Project licensing updated to Apache-2.0.

## [0.7.2] - 2026-08-08

### Fixed
- Browser route-guard failure when handling binary request bodies (e.g.
  gzip-compressed POST payloads), which could cause requests to bypass the
  intended private-address protection. Request bodies are now replayed as
  raw bytes and the SSRF policy stays active for every request. Regression
  tests added at the unit level (binary-body stub) and browser-integration
  level (byte-exact public replay, zero-hit private target).
- `.hermes/plans/` (agent working files) is now gitignored and the tracked
  plan file was untracked; no runtime impact.

## [0.7.1] - 2026-08-08

### Security
- SSRF guard hardened (3 layers): blocks literal private IPs (IPv4/IPv6/
  IPv4-mapped, alternate numerics), hostnames resolving to private targets,
  and every redirect hop on the HTTP path; the browser path now guards
  navigation AND subresource requests hop-by-hop (route.fetch + per-hop
  policy check), so a public page pointing at a private redirect can no
  longer leak local services (server-side hit-counter verified).
- Expired cookies are no longer sent on the HTTP fast path (previously an
  expired profile session still sent its cookie).
- Documented accepted residual risks: DNS rebinding/TOCTOU in hostname
  resolution (issue #9) and Firecrawl's provider-side redirect chain
  (issue #10), in docs/deep-audit-2026-08-06.md.

### Fixed
- Concurrent cache writers can no longer corrupt the cache: each writer now
  uses its own unique tmp file (mkstemp) before the atomic rename, instead
  of a shared `<key>.tmp` (observed real corruption as `Extra data`
  JSONDecodeError; deterministic regression test added).
- Response size cap (25MB) is now enforced inside the streaming loop;
  `client.get()` previously buffered the entire body before the cap could
  apply.
- MCP server: input caps (n<=50, max_chars<=1M, timeout<=120), strategy
  whitelist, and structured error payloads; an invalid strategy or
  firecrawl-without-key no longer raises SystemExit and kills the whole
  server process.
- Bounded concurrency across the ladder (semaphore, default 10,
  `max_concurrency=` override).
- Cache keys are order-independent (cookies/headers sorted before hashing);
  identical URLs fetched once.

## [0.7.0] - 2026-08-06

### Added
- MCP server (`webget_mcp.py`) exposing `search`, `fetch`, and
  `search_fetch` as MCP tools over stdio, so agents (e.g. opencode) can use
  the same acquisition ladder without API keys. Register via the new
  `webget-mcp` entry point or `python webget_mcp.py`.
- `mcp` optional extra (`fastmcp>=2`): `pip install "webget-cli[mcp]"`.
- MCP server tests (`tests/test_mcp_server.py`, `tests/test_mcp_smoke.py`)
  and a dedicated `mcp-test` CI job; lint now covers `webget_mcp.py`.
- README: MCP server section with opencode registration example, and a
  documented limitation (MCP tools do not expose `--profile`/`--cookies`).

### Fixed
- Terminal state now reports the method of the winning ladder reason
  instead of the last ladder step (e.g. `blocked` from HTTP is no longer
  reported as method `crawl4ai`).
- When Crawl4AI is not installed, the ladder no longer clears pending URLs:
  firecrawl fallback still runs and terminal state is written per URL.
- MCP `fetch` guards invalid `strategy` values and `firecrawl` without
  `WEBGET_FIRECRAWL_KEY`, returning an error payload instead of letting
  `SystemExit` kill the whole server process.
- Profile state path test is OS-agnostic (`os.path.join`).

## [0.6.0] - 2026-08-05

### Added
- Packaging readiness for PyPI:
  - PyPI package renamed to `webget-cli` (bare `webget` is taken by an
    unrelated package); CLI command stays `webget`.
  - `browser` optional extra: Crawl4AI/Playwright no longer installed by
    default. `pip install webget-cli[browser]` or
    `uv tool install webget-cli --with webget-cli[browser]`.
  - Clear error when the browser strategy is requested but Crawl4AI is not
    installed (HTTP fast path and search keep working without it).
  - Build metadata: `[project.urls]`, Python 3.13 classifier, `build-system`
    declaration.
- README installation section: pip, uv tool, browser runtime setup.

### Fixed
- Logout now removes cookies only for the target domain and its subdomains,
  preserving unrelated domains in the same profile (issue #5). Uses a
  domain-scoped regex with Playwright `clear_cookies(domain=...)` instead of
  a global clear.

## [0.5.0] - 2026-08-05

### Added
- Session management UX (Phase 4):
  - `webget login <site> --profile NAME` - interactive login: opens a
    visible browser, user authenticates manually, session is persisted on
    Enter. Never stores passwords or fills forms. `--headless` for
    tests/automation.
  - `webget profiles [--json]` - lists profiles with last-used, size, and
    session status (`authenticated` / `expired` / `unknown` / `corrupt`).
    Never exposes cookie values.
  - `webget logout <site> --profile NAME` - clears authentication for one
    domain only, preserving unrelated domains in the same profile. Does not
    delete the profile.
- Hostname validation for `login`/`logout` site URLs.

## [0.4.0] - 2026-08-05

### Changed (pre-release audit)
- Browser is now launched lazily: `auto` strategy tries HTTP first and only
  starts Crawl4AI/Playwright if a URL still needs it. Previously the browser
  launched preemptively even for static pages.
- Profile persistence failures no longer vanish silently: `webget` warns on
  stderr when a session cannot be exported or the profile dir cannot be
  created.
- Packaging: runtime deps (`crawl4ai`, `ddgs`, `httpx`, `trafilatura`,
  `html2text`) declared in `pyproject.toml`; `pip install .` now works.
- CI splits unit tests (pure logic, no runtime deps) from a package smoke
  test that installs from `pyproject.toml`.
- README JSON example corrected to the real shape (dict keyed by URL).

### Fixed
- **Auth ladder correctness** (audit fixes):
  - HTTP path now reads session cookies from a profile's exported
    `storage_state.json`, so `--profile` works on the fast path too.
  - Ladder no longer stops at the first auth failure. HTTP `401`/`403`/login
    page records the reason and falls through to Crawl4AI, then Firecrawl.
  - `403` is only classified as `login_required` when login/session markers
    are present; generic `403` is `blocked`.
  - `authenticated` is `null` for public pages, `true` only when a profile
    session was actually used.
- Profile name validation: rejects path traversal (`../`, `/`, `\`, `~`).
- URL host parsing via `urllib.parse.urlparse` instead of string splitting.
- Crawl4AI retry policy: retries timeouts/transients, skips blind retries on
  `401`/`403`/challenge.

### Added
- `--no-cache` flag: fetch without reading or writing the disk cache
  (private/authenticated fetches).
- Export of persistent profile session state to
  `profiles/<name>/storage_state.json` after a Crawl4AI run.

## [0.3.0] - 2026-08-05

### Added
- Authenticated session layer:
  - `--profile NAME` persistent browser profiles (Playwright persistent
    context) in `~/.local/share/webget/profiles/`.
  - Auth-state classifier: `success | login_required | challenge | blocked |
    error`, surfaced in JSON as `auth` metadata.
  - `--cookies` long alias for `-c`.
- Cache isolation per profile (public vs `campus` vs `work` never collide).

## [0.2.0] - 2026-08-05

### Added
- HTTP fast path: `httpx` + `trafilatura`/`html2text`, no browser needed for
  static pages.
- Fetch router with strategy ladder: `auto` = HTTP → Crawl4AI → Firecrawl
  (Firecrawl only when `WEBGET_FIRECRAWL_KEY` is set).
- Result metadata: `status`, `method`, `cached`, `attempts`.
- `--strategy` flag; `--limit` replaces the dual meaning of `-n`.
- Command aliases: `search`, `fetch`, `search-fetch`.
- Disk cache with TTL + eviction (500 files).

### Fixed
- Failures are never cached (previously a failed crawl could be cached as an
  empty success).
- Crawl4AI `CrawlResult.success` is checked before trusting content.

## [0.1.0] - 2026-08-05

### Added
- Initial release: DuckDuckGo search (`s`), Crawl4AI scrape (`u`), search +
  scrape (`su`), batch stdin, `-c`/`-H`/`-n`/`-t` options, JSON output.
- Zero API keys, unlimited usage.

[0.8.0]: https://github.com/DavidPandleton/webget/compare/v0.7.3...v0.8.0
[0.7.3]: https://github.com/DavidPandleton/webget/compare/v0.7.2...v0.7.3
[0.7.2]: https://github.com/DavidPandleton/webget/compare/v0.7.1...v0.7.2
[0.7.1]: https://github.com/DavidPandleton/webget/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/DavidPandleton/webget/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/DavidPandleton/webget/compare/v0.5.0...v0.6.0
