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

---

# Phase 11 — Security + Correctness Review (2026-08-06, second pass)

Reviewed branch audit/deep-audit against the Phase 1-10 claims. Findings
below are NEW items found during this review, not the original audit.

## New bugs found + fixed

1. **CRITICAL (found, fixed): browser-strategy SSRF bypass via redirect.**
   - Claim in Phase 1-10 report ("SSRF at three layers") was WRONG for the
     browser path: crawl4ai follows redirects inside Chromium and no
     per-hop check existed. A public URL 302->127.0.0.1 leaked private
     content through the crawl4ai strategy (reproduced: success + markdown
     contained "PRIVATE DATA LEAKED").
   - Root cause: Playwright route handlers only fire for the FIRST request
     of a redirect chain (verified experimentally); Crawl4AI does not
     expose a per-hop hook; CrawlResult does not expose the final URL.
   - Fix: `_guard_browser_routes()` - Playwright route guard that (a)
     aborts any request whose URL is private, (b) for navigation requests
     follows redirects MANUALLY via route.fetch(max_redirects=0) checking
     every hop, (c) fulfills the final response. Registered on existing
     contexts + lazy contexts via browser.on("context").
   - Residual risk (documented): a redirect INSIDE a subresource (img/script)
     is not hop-checked; subresource content never reaches markdown output.
   - Regression tests: tests/test_browser_ssrf.py (needs [browser] extra).
   - Mutation-verified: disabling the hop check -> test FAILS (leak).

2. **MEDIUM (found, fixed): response size cap was NOT streaming.**
   - Claim "enforced while streaming" was WRONG: client.get() buffers the
     entire body before aiter_bytes() runs (verified: is_stream_consumed
     True right after get()). A 2GB response would OOM before the cap fired.
   - Fix: client.stream("GET", ...) + aiter_bytes() with the cap inside the
     loop. /oversize (30MB) test proves memory stays bounded (maxrss delta
     < 10MB) and the fetch aborts.
   - Tests: tests/test_size_review.py.

3. **LOW (found, fixed): expired cookies still sent by HTTP path.**
   - _profile_meta reports a session "expired" but fetch_http still sent the
     cookie (server accepted it -> inconsistent). Fix: skip cookies with
     0 <= expires < now when building the cookie jar.
   - Test: test_expired_session_detected.

4. **LOW (found, fixed): dead code in SSRF module.**
   - `_ssrf_hook` (unused) and `_allow_private` (unused) removed.

## Review findings that were NOT bugs

- `127.0.0.1./x` (trailing dot): guard missed it initially -> fixed with
  host.rstrip("."). Now blocked. (trailing-dot FQDN of a literal)
- allow-private env parsing: only exact "1" enables ("true"/"yes"/"" do
  not) - tested.
- MCP subprocess does NOT inherit WEBGET_FIRECRAWL_KEY/WEBGET_ALLOW_PRIVATE:
  the mcp SDK spawns with a SAFE default environment (HOME/PATH/USER/etc).
  This is GOOD for security (MCP server always runs default-blocked, keys
  never leak into the subprocess) but means MCP firecrawl needs explicit
  env in the client config - documented limitation.
- Concurrency: Semaphore with 0/-1 raises or no-ops safely (tested);
  exceptions inside workers release the semaphore (tested); no leaked tasks
  after batch (tested); cancellation does not hang (tested).
- Cache: atomic tmp+os.replace verified via spy; no .tmp leftovers;
  concurrent writers produce valid JSON; readers never see partial writes;
  _write_json (logout) also atomic.

## DNS rebinding / TOCTOU assessment

`_is_private_target()` resolves hostname -> checks IP -> fetch resolves
AGAIN at connect time. DNS rebinding (attacker answers public on check,
private on connect) is theoretically possible. httpx/httpcore expose NO
resolver pinning (verified: AsyncHTTPTransport has no resolver parameter).
Pinning would require a custom network backend, breaks Host/SNI/proxy, and
is fragile. For a local CLI where the operator must voluntarily fetch an
attacker hostname, complexity is not justified. Documented as residual
risk; GitHub issue #9 opened with severity + future approaches.

## Mutation testing (Phase 11 pass)

| Mutation | Result |
|----------|--------|
| SSRF literal check disabled | caught (loopback cases via is_private) |
| hostname resolution returns False | caught (127.1, decimal, hex IPs) |
| semaphore unbounded | caught (3 tests) |
| browser hop check disabled | caught (leak test FAILS) |

## Benchmark after fixes (no regression)

500 URLs http: 55.3 req/s (before: 55.5), p50 ~167-170ms. Streaming cap
and SSRF hop checks add negligible overhead on the local benchmark.

## Final gate (Phase 11)

SECURITY REVIEW: PASS (browser bypass fixed, guard mutation-verified)
CORRECTNESS REVIEW: PASS
PERFORMANCE REVIEW: PASS (no regression)
MCP REVIEW: PASS (leak scan clean, recovery verified)
PACKAGING REVIEW: PASS (build + clean install + smoke from artifact)
READY TO MERGE: YES (branch audit/deep-audit, pending user approval)
READY TO TAG: NO (version decision pending, no bump without approval)
READY TO PUBLISH: NO (requires explicit approval)

---

# Phase 11b — Browser subresource SSRF + Firecrawl review (2026-08-06, third pass)

## 1. Browser subresource redirect SSRF (found, fixed)

**Root cause:** the Phase 11 route guard only hop-checked NAVIGATION
requests; subresources (`<img>`, `<script>`, `<link>`, fetch/XHR) were
checked on their initial URL then passed through with
`route.continue_()`. Because Playwright route handlers fire only for the
FIRST request of a redirect chain (verified experimentally), a subresource
whose 302 landed on 127.0.0.1 was followed inside Chromium with no policy
check. Reproduced: public page with `<img src=/redirect-to-private-page>`
leaked private content through the browser path.

**Implementation:** unified guard in `_guard_browser_routes()` — EVERY
request (navigation AND subresource) is now fetched manually hop-by-hop
via `route.fetch(url, max_redirects=0)` with the SSRF policy checked at
every hop BEFORE the hop is fetched, then the vetted response is
fulfilled. 301/302/303 upgrade to GET per HTTP spec; 307/308 preserve
method+body. Unsupported fetches (e.g. websocket) fall back to
`route.continue_()` after the initial-URL check.

**Proof (server-side counter):** TestServer now counts requests per path
(`hits_for(path)`). Tests assert `hits_for("/private-page") == 0` and
`hits_for("/secret") == 0` — the private endpoints NEVER received a
request, not merely that their response was discarded.

Results:
- top-level navigation redirect into private: BLOCKED (error, 0 hits)
- subresource redirect into private: BLOCKED (0 hits on landing)
- public->public subresource redirect: still works (title resolved)
- direct private URL: blocked before browser (0 hits)
- normal page assets / JS: still render (guard fulfills the real response)

Mutation-verified:
- subresource bypass mutation (non-navigation continue_() early) -> 3 tests FAIL
- hop-check removed mutation -> 4 tests FAIL

## 2. Firecrawl SSRF limitation (reviewed, documented, NOT mitigated)

Verified against docs.firecrawl.dev (2026-08): the Firecrawl /v2/scrape
API has NO parameter to disable redirects, validate redirect
destinations, receive the redirect chain, or enforce allowed hosts/IPs.
`threatProtection` (whitelist/blacklist/blockedTlds) exists but is
ENTERPRISE-only and does not guarantee per-hop validation.

webget guarantees:
- private/internal URLs are never SENT to Firecrawl (pre-check before ladder)
- Firecrawl is strictly opt-in (key + explicit strategy)

webget cannot control: any redirect Firecrawl follows after the initial
URL. This is a REMOTE-provider limitation, distinct from local SSRF
protection. Documented in fetch_firecrawl docstring; GitHub issue #10
opened with severity, threat model, mitigations, future options.

## 3. Strategy invariant (tested)

- HTTP blocks private -> Crawl4AI also blocks (browser route guard)
- Firecrawl never receives a blocked URL (spy test: sent == [])
- Without a key, firecrawl strategy fails clean (SystemExit, no provider call)
- Firecrawl remains opt-in; auto ladder only reaches it with a key

## 4. Verification

- pytest: 292 passed, 1 skipped (browser tests run in the [browser] venv: 5/5 pass)
- ruff check: clean; ruff format --check: clean
- build: wheel + sdist OK
- clean install: CLI fetch OK, SSRF block OK, MCP tools OK
- benchmark: see below

## 5. Benchmark (after unified guard)

- 500 URL http: 50.6-51.1 req/s (baseline ~55.5); run-to-run variance high
  (45.8-51.1) with system load 0.78; p50 ~172-191ms (baseline ~170ms)
- 200 URL concurrent isolation run: ~62 req/s (counter overhead negligible)
- HTTP path does not use the browser guard (guard is browser-only), so the
  small delta is system noise, not the guard. No meaningful regression.
