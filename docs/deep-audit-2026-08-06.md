# webget Deep Architecture + MCP + Adversarial Benchmark Audit

Date: 2026-08-06
Branch: `audit/deep-audit` (based on `main` @ a2a0e04, v0.7.0)
Scope: full codebase (webget_cli.py ~1244 lines, webget_mcp.py ~150 lines),
MCP server, SSRF, concurrency, cache, auth, benchmarks.

## 1. Architecture reviewed

- **CLI** (`webget_cli.py`): single-file. `parse_opts` → dispatch in `main()`
  for `s`/`u`/`su`/`login`/`profiles`/`logout`. JSON output = dict-of-URLs.
- **Search**: `search()` wraps DDGS (sync, called via `asyncio.to_thread`).
- **HTTP fast path**: `fetch_http()` — httpx, local markdown extraction
  (trafilatura → html2text), manual redirect following.
- **Browser fallback**: `scrape_many` Pass 2 — lazy `from crawl4ai import`,
  one persistent context, per-URL `_crawl4ai_once` with one retry.
- **Firecrawl fallback**: Pass 3, opt-in via `WEBGET_FIRECRAWL_KEY`.
- **Ladder**: `_ladder(strategy, key)` → steps; `record()` classifies each
  attempt; `_terminal_state()` picks winner by priority
  challenge > login_required > blocked > error.
- **Auth**: `_auth_state()` heuristic classifier (challenge markers, login
  forms, SION-style labels, 401/403/429), `authenticated` tri-state.
- **Profiles**: Playwright persistent context dirs + exported
  `storage_state.json` for HTTP-path reuse. `login`/`logout`/`profiles`.
- **Cache**: content-level (not strategy-level), keyed by
  profile|url|max_chars|cookies|headers, TTL 1h, eviction cap 500.
- **MCP** (`webget_mcp.py`): FastMCP stdio server, tools
  `search`/`fetch`/`search_fetch`, wrapping the same ladder.

## 2. Findings fixed (with reproduction + regression tests)

### HIGH — SSRF: private targets reachable (HTTP, redirects, MCP)
- Reproduced: `scrape_many(["http://127.0.0.1:<port>/private"])` returned
  success and leaked the private page; redirects followed into loopback;
  cloud-metadata-style path served "PRIVATE DATA LEAKED".
- Root cause: no address policy anywhere; `follow_redirects=True` let httpx
  chase Location into private IPs; MCP exposed the same.
- Fix:
  - `_is_private_target()` — rejects literal private IPs (loopback,
    private, link-local, reserved, multicast, unspecified, IPv4-mapped
    IPv6) AND hostnames resolving to them (cached, capped 512).
  - `fetch_http()` checks initial URL and EVERY redirect hop
    (`follow_redirects=False` + manual loop, max 20 hops).
  - `scrape_many()` pre-checks every URL before any strategy (browser and
    Firecrawl never see private targets), `attempts=0` error result.
  - Escape hatch `WEBGET_ALLOW_PRIVATE=1` for deliberate intranet use.
- Tests: `tests/test_adversarial_ssrf.py` (18 cases incl. IPv6, 0.0.0.0,
  metadata, hostname resolution, hop check, env bypass),
  `TestSSRFLadder` integration, MCP SSRF via subprocess.
- Mutation-verified (guard disabled → tests fail).

### HIGH — Unbounded concurrency (resource exhaustion)
- Reproduced: 500-URL batch → 500 concurrent httpx calls / 500 browser
  pages on one context; `max_active` hit 100+.
- Fix: `asyncio.Semaphore` (`_DEFAULT_CONCURRENCY = 10`, overridable
  `max_concurrency=` param) around every ladder pass.
- Tests: `TestBoundedConcurrency` — 100 URLs capped, custom cap honored.
- Mutation-verified.

### MEDIUM — Cache writes not atomic; concurrent writes can corrupt
- Reproduced: `cache_put` wrote directly to the final path; a crash or a
  concurrent writer leaves truncated JSON (readers tolerate it, but the
  entry is poisoned until TTL).
- Fix: write `*.tmp` + `os.replace`; `_write_json` (logout path) too.
- Tests: `test_concurrent_writes_never_corrupt`, `test_no_partial_file_on_write`
  (spies os.replace). Mutation-verified after strengthening.

### MEDIUM — No response size cap (memory exhaustion)
- Reproduced: 5MB body fully buffered; a giant/binary URL would OOM.
- Fix: `MAX_RESPONSE_BYTES = 25MB` cap while streaming in `fetch_http`.
- Test: `test_huge_response_is_bounded`.

### MEDIUM — Duplicate URLs cause duplicate work + races
- Reproduced: `[u, u]` → 2 attempts, `attempts=2`.
- Fix: dedup in `scrape_many`. Test: `TestDuplicateURLs`. Mutation-verified.

### LOW — Cache key not cookie-order-independent
- Fix: sort cookies/headers in `_cache_path`. Test added.

### LOW — `search()` exceptions crashed CLI with traceback
- Fix: clean `error: search failed: ...` + exit 1 in `s` and `su`.

### LOW — MCP accepted abusive numeric inputs
- `n=10**9`, `max_chars=10**9`, `timeout=-5` now rejected via `_clamp`
  (bounds: n≤50, max_chars≤1M, timeout 1..120). Tests in
  `TestInputCaps`. Mutation-verified.

### LOW — logout pruning logic untestable (embedded in browser flow)
- Extracted `_prune_storage_cookies()` pure function; used by
  `_logout_flow`. Tests: domain scoping incl. suffix-trap cases.

## 3. MCP audit

- Protocol: FastMCP stdio; tools listed/discoverable; results are plain
  JSON text content (valid for agents).
- Reliability: repeated calls (10x same session) OK; concurrent calls (5x
  gather) OK; failure isolation (bogus strategy then good fetch) OK; server
  survives malformed args.
- Input validation: strategy whitelist + numeric bounds + URL pre-check.
- Security: private-URL fetch blocked by default in the MCP subprocess
  (no `WEBGET_ALLOW_PRIVATE` inherited in tests → default policy active).
- Agent usability: result shape carries status/method/cached/attempts/error/
  auth + markdown; search returns title/url/snippet. No ambiguous fields
  found; contract unchanged (backward compatible).
- stdout/stderr: FastMCP owns stdout; warnings go to stderr — no
  contamination observed.

## 4. Testing added

New files: `tests/http_server.py` (deterministic local server: normal,
login, SION login, 401/403/429/500, redirects 301/302/307/308 + loop +
chain + private bait, challenge, slow/timeout, empty/malformed/binary/
json/gzip/huge, cookie-gated, concurrency probe, bench inventory),
`tests/conftest.py` (session server, isolated cache/profiles, allow-private),
`tests/test_adversarial_http.py`, `test_adversarial_auth.py`,
`test_adversarial_cache.py`, `test_adversarial_concurrency.py`,
`test_adversarial_ssrf.py`, `test_adversarial_mcp.py`,
`test_integration_ladder.py`, `scripts/bench_webget.py`.

Total: **200 tests passing** (was 62 before this audit).
Failures discovered: 32 (all reproduced, all fixed).
Mutation testing: 6 mutations — 5 caught directly, 1 caught after the
cache test was strengthened, 1 required adding the MCP caps test.

## 5. Benchmark (local deterministic server)

Cold cache, http strategy, localhost:

| scale | wall (s) | req/s | success | fallback | cached | p50/p95/p99 (ms) |
|------:|---------:|------:|--------:|---------:|-------:|------------------|
| 10    | 1.28     | 7.8   | 6/10    | 0        | 0      | 194 / 202 / 202   |
| 100   | 1.82     | 55.1  | 60/100  | 0        | 0      | 179 / 219 / 222   |
| 500   | 9.00     | 55.5  | 300/500 | 0        | 0      | 169 / 260 / 260   |

Warm cache: p50 drops to ~0.2ms, cached counts 6/60/60 (only successes are
cached — failures never cached by design).

Concurrency scaling (200 URLs): conc 1 → 34.9 req/s; conc 3 → 65.2; conc 10
→ 64.5; conc 50 → 62.4. **Plateau at ~65 req/s**: local extraction is the
bottleneck (CPU-bound trafilatura/html2text), not connections. Default cap
10 is already optimal; raising it gains nothing and costs memory.

Batch vs sequential (100 URLs): batch 63.5 req/s vs sequential 38.6 req/s —
batching is ~1.65x faster, not linear (CPU-bound extraction).

Browser (crawl4ai, 10 URLs, local): ~2 req/s, ~1.3s per page vs ~200ms
HTTP. Browser fallback costs ~4-6x; only worth it when HTTP fails
(JS-rendered/anti-bot sites).

Firecrawl: not benchmarked — requires a paid API key; ladder path is a
single POST and trivially bounded by the same semaphore.

Bottlenecks: (1) synchronous markdown extraction in threads — dominates
per-URL latency; (2) per-URL `cache_get` stat calls add minor overhead;
(3) crawl4ai per-page overhead ~1.3s is inherent to the browser.

## 6. Concurrency audit

- All `asyncio.gather` sites (http/crawl4ai/firecrawl passes) now bounded
  by one semaphore.
- Shared mutable state: only `_PRIVATE_IP_CACHE` (module dict, capped 512,
  written from event-loop context — safe single-threaded).
- Cache eviction: cross-process race possible but best-effort + OSError
  caught; acceptable for a local CLI.
- Profile `storage_state.json`: concurrent processes could interleave
  writes; documented limitation (Playwright persistent contexts also lock
  the dir). Atomic writes now prevent partial-file corruption.
- Browser context: `async with AsyncWebCrawler` guarantees close; verified
  in browser smoke run (context cleaned up between runs).

## 7. Remaining risks / accepted limitations

- DNS-rebinding TOCTOU: hostname resolution is checked before the request;
  a resolver that answers public-then-private could theoretically slip
  through the HTTP path. Mitigation: rare for a local CLI; the pre-check
  still blocks direct private literals and normal private hostnames.
- `WEBGET_ALLOW_PRIVATE=1` disables ALL private checks (deliberate).
- Firecrawl sends the target URL to a third party — documented, opt-in.
- Cache eviction is cross-process racy (best-effort).
- No observability/debug mode (out of scope; would be a MINOR feature).
- Benchmark numbers are localhost-only; real-world latency dominated by
  network, not by webget.

## 8. Versioning

- Current: 0.7.0 (main @ a2a0e04)
- Proposed: **0.7.1 — PATCH**
- Reason: all changes are bug/security/regression fixes + hardening
  (SSRF guard, bounded concurrency, atomic cache, size cap, dedup,
  MCP input caps, error message improvements, test-only additions).
  No new public feature, no breaking change (new `max_concurrency` param
  is optional and backward compatible; `WEBGET_ALLOW_PRIVATE` is opt-in).
- No release/tag/publish performed — awaiting explicit approval.

## 9. Verification (Phase 9)

- pytest: 200 passed
- ruff check (cli + mcp + tests + scripts): clean
- build wheel + sdist: OK
- clean venv install from wheel: OK
- `webget --help`: OK
- CLI search smoke: OK (real DDG)
- CLI fetch smoke (https://example.com): success/http
- MCP tool discovery from installed artifact: search/fetch/search_fetch
- MCP actual invocation from installed artifact: fetch success
- Browser extra: crawl4ai 0.9.2 + chromium-headless-shell installed in
  clean venv; `webget fetch --strategy crawl4ai` → success/crawl4ai
- Browser benchmark executed (2 req/s, ~1.3s/page)

## 10. Architecture verdict

Still simple (single file + MCP shim), boundaries clear (ladder passes,
classifier, cache, profiles), degrades safely (every failure becomes a
recorded reason, ladder continues), SSRF now defended at three layers
(pre-check, hop check, strategy isolation). The single-file design is a
feature at this scale; the abstractions proposed (provider registry,
typed error taxonomy) were considered and NOT implemented — they would add
complexity without a measurable benefit for a ~1250-line tool. The one
worthwhile extraction done: `_prune_storage_cookies` (testability).
