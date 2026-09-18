"""Tests for the multi-engine search layer (ddgs metasearch backends).

ddgs is a metasearch: DDGS().text(backend=...) selects which of its 9
keyless engines run. These tests pin webget's contract on top of that
semi-internal kwarg: engine selection must be validated at runtime and
invalid names degrade to auto instead of raising.

Note: in the webget package, `webget.search` is the re-exported FUNCTION
(from .search import search), which shadows the submodule attribute, so
these tests import the helpers straight from the module and patch ddgs
where the lookup actually happens (inside search(), `from ddgs import DDGS`).
"""

from typing import ClassVar

import webget_cli as webget
from webget.search import _resolve_engine, known_engines


class FakeDDGS:
    """Records the backend kwarg ddgs actually received."""

    captured: ClassVar[dict] = {}

    def __init__(self, *a, **kw):
        pass

    def text(self, query, max_results=None, **kw):
        FakeDDGS.captured["backend"] = kw.get("backend")
        FakeDDGS.captured["max_results"] = max_results
        return [{"title": "t", "href": "https://e.example/a", "body": "b"}]


def _patch_ddgs(monkeypatch):
    FakeDDGS.captured = {}
    monkeypatch.setattr("ddgs.DDGS", FakeDDGS)
    # Keep the engine-health ledger out of the user's state dir. Tests that
    # exercise search() record real observations otherwise, and since the
    # ledger reorders failover, a run would depend on the previous run's file.
    import tempfile
    from pathlib import Path

    monkeypatch.setenv(
        "WEBGET_ENGINE_HEALTH",
        str(Path(tempfile.mkdtemp()) / "engine_health.json"),
    )


class TestSearchEngineValidation:
    def test_valid_engine_returns_normalized_results(self, monkeypatch):
        _patch_ddgs(monkeypatch)
        out = webget.search("q", n=1, engine="brave")
        assert FakeDDGS.captured["backend"] == "brave"
        assert out == [{"title": "t", "url": "https://e.example/a", "snippet": "b"}]

    def test_engine_none_is_auto_and_backward_compatible(self, monkeypatch):
        _patch_ddgs(monkeypatch)
        out = webget.search("q", n=5)
        # no backend kwarg at all = ddgs default (auto), unchanged behavior
        assert FakeDDGS.captured["backend"] is None
        assert out[0]["url"] == "https://e.example/a"

    def test_invalid_engine_degrades_to_auto_not_raise(self, monkeypatch):
        _patch_ddgs(monkeypatch)
        out = webget.search("q", engine="not-a-real-engine")
        assert out and out[0]["url"] == "https://e.example/a"
        assert FakeDDGS.captured["backend"] == "auto"

    def test_comma_delimited_subset_passes_through(self, monkeypatch):
        _patch_ddgs(monkeypatch)
        webget.search("q", engine="brave,duckduckgo")
        assert FakeDDGS.captured["backend"] == "brave,duckduckgo"

    def test_subset_with_one_bad_name_keeps_the_good_one(self, monkeypatch):
        _patch_ddgs(monkeypatch)
        webget.search("q", engine="brave,bogus")
        assert FakeDDGS.captured["backend"] == "brave"

    def test_explicit_auto_keyword_maps_to_auto(self, monkeypatch):
        _patch_ddgs(monkeypatch)
        webget.search("q", engine="auto")
        assert FakeDDGS.captured["backend"] == "auto"

    def test_known_engines_validated_against_live_registry(self):
        names = known_engines()
        assert isinstance(names, list)
        # The ddgs registry shifts between releases (9.14 had yandex, 9.16
        # dropped it), so only assert the stable subset, never a fixed count.
        assert "duckduckgo" in names
        assert "brave" in names
        assert len(names) >= 5


class TestResolveEngine:
    def test_empty_and_none_stay_none(self):
        assert _resolve_engine(None) is None
        assert _resolve_engine("") is None
        assert _resolve_engine("   ") is None

    def test_auto_and_all(self):
        assert _resolve_engine("auto") == "auto"
        assert _resolve_engine("all") == "auto"

    def test_unknown_returns_auto(self):
        assert _resolve_engine("bogus") == "auto"

    def test_known_single(self):
        assert _resolve_engine("mojeek") == "mojeek"
