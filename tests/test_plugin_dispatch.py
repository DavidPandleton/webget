"""Unit tests for webget plugin discovery and dispatch."""

import os
import stat

from webget.plugin import _PLUGIN_NAME_RE, find_plugin, list_plugins


def test_plugin_name_validation():
    assert _PLUGIN_NAME_RE.match("kampus")
    assert _PLUGIN_NAME_RE.match("foo-bar_123")
    assert not _PLUGIN_NAME_RE.match("../evil")
    assert not _PLUGIN_NAME_RE.match("foo/bar")
    assert not _PLUGIN_NAME_RE.match("foo;rm")


def test_plugin_discovery_and_dispatch(tmp_path, monkeypatch):
    # Setup temporary plugin directory and isolate PATH
    pdir = tmp_path / "plugins"
    pdir.mkdir()
    monkeypatch.setattr("webget.plugin.PLUGIN_DIR", str(pdir))
    monkeypatch.setenv("PATH", "")

    # Before creating plugin
    assert find_plugin("demo") is None
    assert list_plugins() == []

    # Create an executable mock plugin
    plugin_bin = pdir / "webget-demo"
    plugin_bin.write_text('#!/bin/sh\necho "DEMO PLUGIN: $@"\n')
    plugin_bin.chmod(plugin_bin.stat().st_mode | stat.S_IEXEC)

    # Discovery check
    found = find_plugin("demo")
    assert found == str(plugin_bin)

    plugins = list_plugins()
    assert len(plugins) == 1
    assert plugins[0]["name"] == "demo"
    assert plugins[0]["binary"] == "webget-demo"
    assert plugins[0]["path"] == str(plugin_bin)


def test_plugin_path_search(tmp_path, monkeypatch):
    # Test discovery via PATH
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    plugin_bin = bin_dir / "webget-echo"
    plugin_bin.write_text("#!/bin/sh\necho OK\n")
    plugin_bin.chmod(plugin_bin.stat().st_mode | stat.S_IEXEC)

    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr("webget.plugin.PLUGIN_DIR", str(tmp_path / "nonexistent"))

    found = find_plugin("echo")
    assert found == str(plugin_bin)

    plugins = list_plugins()
    matching = [p for p in plugins if p["name"] == "echo"]
    assert len(matching) == 1
    assert matching[0]["path"] == str(plugin_bin)
