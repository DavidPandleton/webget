# Lightweight Web Acquisition Roadmap

**Goal:** Evolve webget into a lightweight, Docker-free local acquisition tool while keeping browser, external search backends, LLM extraction, and larger crawl features optional.

**Architecture:** Keep the core single-process and dependency-light: HTTP fetch, normalized search contract, Markdown conversion, cache, SSRF policy, provenance, and MCP stdio. Add optional adapters for SearXNG and other providers without bundling their services. Use SQLite for the local resumable crawl frontier; keep Crawl4AI/Playwright behind the browser extra.

## Progress snapshot (2026-09-24)

- ✅ MCP search contract: canonical `limit` plus bounded compatibility alias `n`.
- ✅ Search provider seam: `SearchProvider`, embedded `ddgs` provider, and optional HTTP `SearxngSearchProvider` via `WEBGET_SEARXNG_URL` or an explicit endpoint.
- ✅ Current-state documentation and package metadata updated.
- ✅ Dependency-light structured extraction: JSON-LD and HTML tables with explicit `success`, `incomplete`, and `error` states.
- ✅ Resumable SQLite crawler: deduplication, same-domain policy, depth/page budgets, stale lease recovery, page persistence, JSONL/Markdown export, Python API, CLI, and MCP `crawl` tool.
- ✅ Verification evidence: full offline suite passes, Ruff passes, wheel/sdist build passes, clean Python 3.11 install passes, and MCP stdio smoke passes from a neutral working directory.
- ⏳ Live SearXNG smoke remains environment-dependent because no `WEBGET_SEARXNG_URL` is configured in the current runner.

## Guardrails

- Do not modify the user's dirty main worktree changes (`webget/http.py`, `tests/test_feed_markdown.py`).
- Workers use isolated worktrees and branches.
- No Docker, Redis, Postgres, daemon, or mandatory browser dependency in core.
- Preserve existing public Python/CLI behavior unless a migration alias is explicitly added.
- Every behavioral change needs focused tests and a mutation/regression check where practical.
- Do not merge worker output without reviewing diff, tests, and dependency impact.

## Tranche 1: Contract and provider seams

1. ✅ Stabilize MCP search result-count naming. `limit` is canonical and `n` remains a bounded compatibility alias, with schema tests.
2. ✅ Introduce a small internal search-provider protocol/normalized response boundary. `ddgs` remains the default embedded provider; SearXNG is an optional configured HTTP adapter.
3. ✅ Update current-state docs only after implementation is verified. Historical audit notes remain unchanged.

## Tranche 2: Optional extraction

- ✅ Add a normalized document representation and dependency-light structured extraction primitives for JSON-LD and HTML tables.
- Keep LLM extraction behind provider adapters and optional extras.
- Require schema validation and explicit incomplete/error states.

## Tranche 3: Resumable local crawl

- ✅ Add a SQLite-backed frontier with URL deduplication, depth/domain/page budgets, timeout, resume, stale lease recovery, partial failure records, page persistence, and JSONL/Markdown export.
- ✅ Expose bounded crawl through the Python API, CLI (`webget crawl URL DB`), and MCP (`crawl`).
- Keep browser rendering optional per URL; static pages use the HTTP path.
- Do not add distributed workers or external databases.

## Verification gate

- ✅ Focused tests per tranche.
- ✅ Full offline suite and Ruff.
- ✅ MCP stdio smoke from a neutral working directory.
- ✅ Verify installed package and generated MCP schema after release build/install.
- ✅ Confirm core install remains free of browser, Docker, Redis, Postgres, and LLM SDK requirements.
- ⏳ Run live SearXNG smoke when an endpoint is explicitly configured.
