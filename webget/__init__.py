"""webget - local search + scrape, zero API keys, unlimited usage.

A package refactor of the original single-file webget_cli.py. Public API
is re-exported here so callers can do `from webget import fetch_http,
scrape_many, ...` instead of importing internal modules.

Sub-modules:
  - cache     - disk cache + cookie/header parsing
  - ssrf      - private-IP guard for HTTP + browser paths
  - profile   - persistent browser-profile (auth session) management
  - http      - fast-path HTTP fetch + markdown extraction
  - firecrawl - optional cloud escape-hatch fetch
  - search    - DuckDuckGo text search + atomic JSON helpers
  - ladder    - HTTP -> Crawl4AI -> Firecrawl orchestration
  - cli       - argparse entry point

The legacy single-file import `import webget_cli as webget` keeps
working via the compatibility shim at ./webget_cli.py.
"""

from __future__ import annotations

from .cache import (
    _CACHE_SWEEP_TTL,
    CACHE_DIR,
    _cache_path,
    cache_get,
    cache_put,
    parse_cookie_file,
    parse_headers,
)
from .cli import main, parse_opts
from .discovery import discover_urls
from .firecrawl import fetch_firecrawl, firecrawl_key
from .http import MAX_RESPONSE_BYTES, ResponseTooLarge, _extract_markdown, fetch_http
from .ladder import (
    _DEFAULT_CONCURRENCY,
    _STRATEGY_MEMORY_TTL,
    _auth_message,
    _crawl4ai_once,
    _ladder,
    _learn_strategy,
    _load_strategy_memory,
    _normalize_hit,
    _reorder_steps_by_domain,
    _save_strategy_memory,
    _strategy_memory_path,
    _terminal_state,
    scrape_many,
)
from .profile import (
    _PROFILE_NAME_RE,
    PROFILE_DIR,
    _auth_state,
    _cookie_belongs_to,
    _domain_match,
    _effective_cookies,
    _fmt_age,
    _login_flow,
    _logout_domain_regex,
    _logout_flow,
    _profile_meta,
    _profile_root,
    _prune_storage_cookies,
    _valid_site_url,
    _wait_for_session_cookies,
    _warn,
    list_profiles,
    load_profile_cookies,
    profile_dir,
    profile_exists,
    profile_state_path,
)
from .search import _read_json, _write_json, search
from .ssrf import (
    SSRFError,
    _guard_browser_routes,
    _hostname_private,
    _ip_is_private,
    _is_private_target,
    _private_ip_for,
    _request_body_bytes,
    _resolve_hostname_ips,
)

# Sorted to satisfy ruff RUF022; module grouping lives in the imports above.
__all__ = [
    "CACHE_DIR",
    "MAX_RESPONSE_BYTES",
    "PROFILE_DIR",
    "_CACHE_SWEEP_TTL",
    "_DEFAULT_CONCURRENCY",
    "_PROFILE_NAME_RE",
    "_STRATEGY_MEMORY_TTL",
    "ResponseTooLarge",
    "SSRFError",
    "_auth_message",
    "_auth_state",
    "_cache_path",
    "_cookie_belongs_to",
    "_crawl4ai_once",
    "_domain_match",
    "_effective_cookies",
    "_extract_markdown",
    "_fmt_age",
    "_guard_browser_routes",
    "_hostname_private",
    "_ip_is_private",
    "_is_private_target",
    "_ladder",
    "_learn_strategy",
    "_load_strategy_memory",
    "_login_flow",
    "_logout_domain_regex",
    "_logout_flow",
    "_normalize_hit",
    "_private_ip_for",
    "_profile_meta",
    "_profile_root",
    "_prune_storage_cookies",
    "_read_json",
    "_reorder_steps_by_domain",
    "_request_body_bytes",
    "_resolve_hostname_ips",
    "_save_strategy_memory",
    "_strategy_memory_path",
    "_terminal_state",
    "_valid_site_url",
    "_wait_for_session_cookies",
    "_warn",
    "_write_json",
    "cache_get",
    "cache_put",
    "discover_urls",
    "fetch_firecrawl",
    "fetch_http",
    "firecrawl_key",
    "list_profiles",
    "load_profile_cookies",
    "main",
    "parse_cookie_file",
    "parse_headers",
    "parse_opts",
    "profile_dir",
    "profile_exists",
    "profile_state_path",
    "scrape_many",
    "search",
]
