# Changelog

All notable changes to webget are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/) and [SemVer](https://semver.org/).

## [Unreleased]

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
