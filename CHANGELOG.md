# Changelog

All notable changes to webget are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/) and [SemVer](https://semver.org/).

## [Unreleased]

### Added
- Browser discovery for the crawl4ai pass (`webget/browser.py`). Instead of always
  using Playwright's bundled Chromium, webget now looks for a Chromium-family
  browser already installed and uses it when one can actually be driven. The
  resolution order is explicit intent first: `WEBGET_BROWSER_CDP`, then
  `WEBGET_BROWSER_CHANNEL`, then `WEBGET_BROWSER_PATH`, then a detected browser,
  then the bundled download. Nothing runs at install time, because a browser can
  be added or removed after `pip install` and an install-time probe would be
  stale by the time it matters.
- `webget doctor` prints the resolved browser, every browser found on the
  machine, which of them are usable, the crawl4ai and Playwright-cache state,
  and any environment overrides in effect. `--json` for machine-readable output.
  This exists because "the crawl behaved differently on my machine" is the most
  common question and the answer is usually a browser that was detected but
  cannot be launched.
- `WEBGET_BROWSER_CDP` to attach to an already-running browser over CDP. This is
  the only route that works for browsers Playwright has no channel for. It is
  deliberately opt-in and never auto-detected: attaching to a browser the user is
  logged into mixes their personal session cookies into crawl output.

### Fixed
- The browser pass silently used Playwright's bundled Chromium even when Chrome
  or Edge was installed, downloading ~150MB that was already present on disk.

### Notes
- Detection is broader than usability, and webget now says so instead of
  pretending. crawl4ai 0.9.2 forwards only `channel` to Playwright
  (`browser_manager._build_browser_args()` emits `{"headless", "args",
  "channel"}` and calls `playwright.chromium.launch(**browser_args)`), so
  `executable_path` is unreachable and `BrowserConfig(executable_path=...)`
  raises TypeError. Consequence: Brave, Vivaldi and Opera are detected but
  cannot be launched by crawl4ai; `webget doctor` reports them as NOT USABLE
  with the reason and the CDP workaround. System Firefox is never selected
  either, because Playwright drives Firefox over WebDriver BiDi with its own
  patched build.

## [0.14.0] - 2026-09-19

### Added
- Learned engine health for failover ordering (`webget/health.py`). Every search observation (which engine answered, how fast) folds into a per-install ledger at `~/.local/state/webget/engine_health.json` (override: `WEBGET_ENGINE_HEALTH`). When the requested engine fails, alternates are tried best-first by learned score instead of registry order. The ledger is advisory - it reorders candidates, never removes them - and it decays: entries older than 7 days score as unseen, because the blocking that made an engine dead is exactly what changes. Latency is penalized via EWMA (cap 8s, weight 0.25), so a reliable-but-glacial engine ranks below a fast one. There is no baked-in ranking in the source: the same install learns a different order on different networks. Diagnostics: `health.health_snapshot()`.
- Time-budgeted failover (`FAILOVER_BUDGET_S = 15.0`, env `WEBGET_FAILOVER_BUDGET_S`), replacing the fixed 3-attempt cap. A count is a bad proxy for "enough": 3 x 20s timeouts is a minute of dead air, while 8 fast engines finish in 3s and a live engine fifth in line never got reached. At least `MIN_FAILOVER_ATTEMPTS = 2` alternates are always tried regardless of the clock, and the budget starts after the requested engine's own attempt, so one slow first call cannot silently disable the safety net. Provenance gains `budget_exhausted` and `untried` so a budget-limited failure is distinguishable from "every engine is dead"; budget exhaustion is announced on stderr.
- Provenance gains `health_ranked`: True when the failover order came from the learned ledger rather than registry order.

### Fixed
- `search_with_provenance` crashed with UnboundLocalError on every successful first-engine call (the `_health_ranked` variable was defined after `_prov` could read it), and the failure was swallowed by failover, so every search silently burned a second engine call. Found by driving the installed artifact, not by the suite.
- The CLI's user-facing help did not document `--engine` or most options: the module docstring carrying them was overwritten by a shorter `__doc__ = (...)` assignment further down the file, so the option documentation written for 0.13.0 was invisible for the entire release line. The dead docstring is gone; the live one now documents all options. `webget s --help` previously ran a search for the word "help" instead of showing usage - `--help` is now honoured after subcommands.
- MCP tests pinned one MCP SDK spelling: `CallToolResult.isError` (mcp 1.x / fastmcp 3.x) vs `is_error` (mcp 2.x / fastmcp 4.x). A `tool_failed()` helper in conftest reads whichever exists, and the suite now passes on both fastmcp 3.4.7 and 4.0.5. `mcp` extra floor raised to `fastmcp>=4` for installs (the code path never used the old name; only the tests did).
- Test isolation: the search fixtures wrote real engine-health observations into the user's `~/.local/state` and read whatever the previous run left there, making outcomes depend on disk state outside the repo (the same suite passed and failed on an unchanged tree). The ledger path is now redirected per-test. Multi-engine labels like `brave,duckduckgo` are recorded against each named engine instead of being stored as one dead ledger key.

### Changed
- CI reshaped: `test` (offline unit, 3 Python versions, plus a socket-blocked run proving no test touches the network), `mcp-test` (offline MCP surface), `network` (non-gating, runs the `live_network`-marked tests and the stratified engine benchmark), `artifact` (installs the built wheel in a clean venv and runs `scripts/artifact_smoke.py` against it - the check that catches the bugs above, which are invisible to source-tree tests), and `build` (sdist/wheel + twine check). Tests that genuinely reach the public internet carry a `live_network` marker (verified empirically by blocking sockets except localhost; exactly three tests failed without a network and they are the three marked).
- New tooling: `scripts/bench_engines_stratified.py` (102 queries across 8 strata - docs/code/news/commerce/science/local plus Tranco head/tail - with per-stratum success rates, top-1 accuracy where ground truth exists, and cross-engine agreement; the old 8-query benchmark could not support the reliability claims made from it), `scripts/classify_tests.py` (empirical network-dependence classification), `scripts/find_live_tests.py` (MCP live-test detection), and `scripts/artifact_smoke.py` (installed-artifact smoke test; non-gating online checks are separated from gating offline ones).

## [0.13.0] - 2026-09-18

### Added
- Multi-engine search: `webget s`/`su` accept `--engine` (or `-e`) and the MCP `search`/`search_fetch` tools accept an `engine` parameter. The `ddgs` dependency is a metasearch that aggregates 9 keyless engines (brave, duckduckgo, google, mojeek, startpage, wikipedia, yahoo, yandex and more); the default `auto` runs all of them, while a comma-delimited subset like `--engine brave,duckduckgo` is roughly 3-6x faster and routes around a single engine having a bad hour. Engine names are validated against ddgs's runtime registry and unknown names degrade to `auto` with a warning instead of failing, so a typo or a registry change between releases never kills a search.
- Search failover: when the selected engine raises OR returns zero results, webget tries up to 3 further engines and returns the first non-empty set. The substitution is always announced on stderr, and the original error is re-raised when nothing works, so the real cause (TLS, 429) survives instead of a generic "all engines failed". This matters because engine health is host-specific: a benchmark from the author's network found only 1 of 9 engines reachable, and `auto` survived only because that one did. Bounded at 3 attempts so worst-case latency stays near the single-engine case.
- Engine provenance on search results. `webget s q --json` (the JSON output path did not previously exist for `s` at all) and the MCP `search`/`search_fetch` tools now report `engine` (the engine that actually answered), `requested_engine`, and `failed_over`. The Python API gets `search_with_provenance()`, which returns `(results, provenance)`; `search()` is unchanged and still returns a bare list. Provenance is per-CALL, not per-result: ddgs merges every engine's hits into one list before returning and its TextResult carries only title/href/body, so the originating engine of an individual hit is discarded upstream. Per-call is what matters for failover anyway. This is the fix for the gap failover opened: the stderr warning is invisible over MCP and lost when output is piped, so without these fields an agent cannot tell that the results came from a different engine than it asked for.

### Changed
- **MCP `search` and `search_fetch` now return an object** (`{"results": [...], "engine": ..., "requested_engine": ..., "failed_over": ...}`) instead of a bare list. This is a breaking change for MCP clients that consumed the old list shape; it is required because a list cannot carry provenance. Error responses are likewise now `{"error": ..., "results": []}`.

## [0.12.1] - 2026-09-16

### Fixed
- Extract: oversized inline base64 image payloads (`data:image/...;base64,` with a payload over 200 chars) are replaced with `stripped` in HTTP fast-path markdown. The markdownify fallback used to keep them verbatim, so a single hero image could burn hundreds of KB of tokens as unreadable noise. Alt text and the mime prefix are preserved; short payloads are left untouched.

## [0.12.0] - 2026-09-16

### Added
- Metadata: fetch results now carry a `metadata` dict (`author`, `published_at`, `site_name`, `language`) extracted via trafilatura on the HTTP fast path, exposed in `scrape_many` output, CLI `--json`, and MCP `fetch`/`search_fetch`. Values are `null` when unknown or when the winning strategy was not HTTP.
- Non-HTML routing: the HTTP fast path converts JSON (pretty code block), CSV (GFM table), RSS/Atom feeds (link list), PDFs (per-page text via new `pypdf` core dep), and plain text instead of erroring `not HTML`. Valid non-HTML payloads skip the 100-char thin-check in the ladder.

## [0.11.0] - 2026-09-03

### Added
- SSRF guard: Dual-resolver DNS fallback via DoH (Cloudflare 1.1.1.1 / Google 8.8.8.8) when system `getaddrinfo` raises `socket.gaierror`, preventing false-negative unreachable errors on valid infrastructure domains while enforcing SSRF checks on resolved IPs.
- Ladder & CLI: Opt-in transient retry pass (`--retry` / `-r` / `retry_transient=True` in `scrape_many`) to automatically retry HTTP fast-path requests that encounter transient timeouts before triggering expensive browser fallback.
- URL Discovery: `webget map <url>` command and MCP `map` tool for fast sitemap and `robots.txt` URL discovery.

## [0.10.0] - 2026-09-01

### Changed
- Internal: split `webget_cli.py` (1762 lines) into a `webget/` package
  with focused sub-modules (`cache`, `ssrf`, `profile`, `http`,
  `firecrawl`, `search`, `ladder`, `cli`). `webget_cli.py` is kept as a
  ~50-line backwards-compatible shim that re-exports the public API, so
  `import webget_cli as webget` and the full test fixture (`monkeypatch
  .setattr(webget, ...)`) keep working unchanged. Entry point migrated
  to `webget.cli:main`.

## [0.9.0] - 2026-09-01

### Added
- MCP server: `login` tool opens a browser session for a profile, navigates
  to a URL, and persists the session without blocking on stdin (the MCP
  stdin is the JSON-RPC stream, so the flow polls the browser context until
  session cookies appear instead of waiting for an Enter keypress). Invalid
  URLs, profile names, and out-of-range `wait_seconds` are clean errors.
- CLI: `--concurrency N` flag for batch runs (`webget u -` / `webget su`),
  plumbing the existing `max_concurrency` parameter of `scrape_many` to the
  command line (default stays 10).

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

[0.13.0]: https://github.com/DavidPandleton/webget/compare/v0.12.1...v0.13.0
[0.12.1]: https://github.com/DavidPandleton/webget/compare/v0.12.0...v0.12.1
[0.12.0]: https://github.com/DavidPandleton/webget/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/DavidPandleton/webget/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/DavidPandleton/webget/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/DavidPandleton/webget/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/DavidPandleton/webget/compare/v0.7.3...v0.8.0
[0.7.3]: https://github.com/DavidPandleton/webget/compare/v0.7.2...v0.7.3
[0.7.2]: https://github.com/DavidPandleton/webget/compare/v0.7.1...v0.7.2
[0.7.1]: https://github.com/DavidPandleton/webget/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/DavidPandleton/webget/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/DavidPandleton/webget/compare/v0.5.0...v0.6.0
