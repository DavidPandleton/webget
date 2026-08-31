"""MCP login tool + non-interactive login flow tests.

Covers:
  - _login_flow(interactive=False): polls browser context until a Set-Cookie
    arrives (here: /set-cookie on the local test server), then persists a
    storage_state.json the HTTP fast path can reuse.
  - MCP login tool: validates URL/profile, runs the flow headless against
    the local server, then fetch(profile=...) uses the stored session.
  - login tool rejects bad URLs and unknown/invalid profiles.

The test server lives on 127.0.0.1, so WEBGET_ALLOW_PRIVATE=1 is required
for the subsequent authenticated fetch through the SSRF-guarded HTTP path.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import webget_cli as webget

playwright = pytest.importorskip("playwright", reason="playwright not installed")


def _profile_root(tmp_path):
    return tmp_path / "profiles"


def test_login_flow_noninteractive_persists_session(server, tmp_path, monkeypatch):
    """Browser-less-enough path: headless Chromium navigates /set-cookie,
    cookie polling sees the Set-Cookie side effect, storage_state persists,
    and the HTTP fast path can reuse it on /cookie-gated."""
    root = _profile_root(tmp_path)
    monkeypatch.setattr(webget, "PROFILE_DIR", str(root))
    monkeypatch.setenv("WEBGET_ALLOW_PRIVATE", "1")

    asyncio.run(
        webget._login_flow(
            server.url("/set-cookie"),
            "flowp",
            headless=True,
            wait_seconds=30,
            interactive=False,
            quiet=True,
        )
    )

    state_p = webget.profile_state_path("flowp")
    assert os.path.exists(state_p)
    state = json.loads(Path(state_p).read_text())
    cookies = {c["name"]: c["value"] for c in state["cookies"]}
    assert cookies.get("webget_session") == "1"

    # HTTP fast path must now authenticate on the cookie-gated page.
    res = asyncio.run(
        webget.scrape_many(
            [server.url("/cookie-gated")],
            strategy="http",
            no_cache=True,
            profile="flowp",
        )
    )
    out = res[server.url("/cookie-gated")]
    assert out["status"] == "success"
    assert out["auth"]["authenticated"] is True
    assert "you are authenticated" in out["markdown"]


def test_login_flow_waits_until_cookie(server, tmp_path, monkeypatch):
    """A page that does NOT set a cookie still persists (best-effort), and a
    slow Set-Cookie is picked up by polling before the deadline."""
    root = _profile_root(tmp_path)
    monkeypatch.setattr(webget, "PROFILE_DIR", str(root))

    asyncio.run(
        webget._login_flow(
            server.url("/set-cookie"),
            "slowp",
            headless=True,
            wait_seconds=30,
            interactive=False,
            quiet=True,
        )
    )
    state_p = webget.profile_state_path("slowp")
    assert os.path.exists(state_p)
    cookies = {c["name"] for c in json.loads(Path(state_p).read_text())["cookies"]}
    assert "webget_session" in cookies


def _spawn_mcp(profile_root):
    from mcp import StdioServerParameters

    env = {
        **os.environ,
        "WEBGET_PROFILE_DIR": str(profile_root),
        "WEBGET_ALLOW_PRIVATE": "1",
    }
    return StdioServerParameters(command=sys.executable, args=[str(ROOT / "webget_mcp.py")], env=env)


def test_mcp_login_tool_persists_and_fetch_uses_it(server, tmp_path):
    """End-to-end MCP: login(url, profile, headless=True) against the local
    /set-cookie page persists a session, then fetch(profile=...) on the
    cookie-gated page succeeds while anonymous fetch does not."""
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    root = _profile_root(tmp_path)
    login_url = server.url("/set-cookie")
    gated_url = server.url("/cookie-gated")

    async def run():
        async with stdio_client(_spawn_mcp(root)) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool(
                "login",
                {"url": login_url, "profile": "mcplogin", "headless": True, "wait_seconds": 30},
            )
            authed = await session.call_tool(
                "fetch", {"url": gated_url, "strategy": "http", "no_cache": True, "profile": "mcplogin"}
            )
            anon = await session.call_tool(
                "fetch", {"url": gated_url, "strategy": "http", "no_cache": True}
            )
            return res, authed, anon

    res, authed, anon = asyncio.run(asyncio.wait_for(run(), timeout=90))
    res_text = res.content[0].text if res.content else ""
    assert not res.is_error, res_text
    assert '"status":"success"' in res_text, res_text
    assert '"profile":"mcplogin"' in res_text

    authed_text = authed.content[0].text if authed.content else ""
    assert '"status":"success"' in authed_text, authed_text
    assert "you are authenticated" in authed_text
    assert '"authenticated":true' in authed_text

    anon_text = anon.content[0].text if anon.content else ""
    assert '"status":"success"' not in anon_text, f"anonymous fetch leaked: {anon_text}"


def test_mcp_login_rejects_bad_url_and_profile(server, tmp_path):
    """login tool validates its inputs: bad URL and bad profile name are
    clean errors, never a crash."""
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    root = _profile_root(tmp_path)

    async def run():
        async with stdio_client(_spawn_mcp(root)) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            bad_url = await session.call_tool(
                "login", {"url": "not a url", "profile": "x", "headless": True, "wait_seconds": 5}
            )
            bad_name = await session.call_tool(
                "login", {"url": server.url("/"), "profile": "../evil", "headless": True, "wait_seconds": 5}
            )
            bad_secs = await session.call_tool(
                "login", {"url": server.url("/"), "profile": "x", "headless": True, "wait_seconds": 1}
            )
            return bad_url, bad_name, bad_secs

    bad_url, bad_name, bad_secs = asyncio.run(asyncio.wait_for(run(), timeout=60))
    assert "invalid site URL" in bad_url.content[0].text
    assert "invalid profile name" in bad_name.content[0].text
    assert "wait_seconds must be between 5 and 600" in bad_secs.content[0].text


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
