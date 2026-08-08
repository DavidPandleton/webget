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
