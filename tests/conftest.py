"""Shared fixtures: local test server, isolated cache/profile dirs, env isolation."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import webget_cli as webget
from tests.http_server import TestServer


@pytest.fixture(scope="session")
def server():
    """Local deterministic HTTP server (started once for the whole session)."""
    srv = TestServer().start()
    yield srv
    srv.stop()


def tool_failed(result):
    """True when an MCP CallToolResult reports an error, across SDK versions.

    The MCP SDK renamed CallToolResult.isError to is_error in the 2.x line:
    fastmcp 3.x pulls mcp 1.x (isError only) and fastmcp 4.x pulls mcp 2.x
    (is_error only). Reading either name directly pins the suite to one SDK,
    so this reads whichever exists. Getting this wrong is not subtle - 20
    tests fail on the other version - which is why it lives in one place
    instead of in each test file.
    """
    for attr in ("is_error", "isError"):
        val = getattr(result, attr, None)
        if val is not None:
            return bool(val)
    raise AssertionError(
        f"MCP CallToolResult exposes neither is_error nor isError: {type(result)!r}"
    )


@pytest.fixture()
def isolated_env(tmp_path, monkeypatch):
    """Point cache + profile dirs at tmp_path and clear FIRECRAWL key."""
    monkeypatch.setenv("WEBGET_FIRECRAWL_KEY", "")
    cache = tmp_path / "cache"
    profiles = tmp_path / "profiles"
    cache.mkdir()
    profiles.mkdir()
    monkeypatch.setattr(webget, "CACHE_DIR", str(cache))
    monkeypatch.setattr(webget, "PROFILE_DIR", str(profiles))
    return {"cache": str(cache), "profiles": str(profiles)}


@pytest.fixture()
def allow_private(monkeypatch):
    """Permit fetching 127.0.0.1 in tests that target the local server
    through the guarded HTTP path (SSRF guard default-blocks private IPs)."""
    monkeypatch.setenv("WEBGET_ALLOW_PRIVATE", "1")
    yield


@pytest.fixture()
def fresh_cache(server, isolated_env, allow_private):
    """Server + isolated cache + private allowed: the common happy path."""
    return server
