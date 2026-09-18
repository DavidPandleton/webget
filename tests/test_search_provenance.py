"""Tests for engine provenance on search results.

Why this exists: failover means the results a caller receives may come from
a DIFFERENT engine than the one requested. Today the only notice is a stderr
warning, which is lost the moment output is piped or the call goes through
MCP (an agent never sees stderr). Provenance puts that fact in the payload.

Scope, established by reading ddgs's source (ddgs/results.py, ddgs/ddgs.py):
  - Per-RESULT provenance is NOT possible. ResultsAggregator merges every
    engine's hits into one list and TextResult only carries title/href/body,
    so which engine produced a given hit is discarded upstream.
  - Per-CALL provenance IS possible, and it is the thing that actually
    matters for failover: webget knows which engine it attempted and which
    one returned.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from webget.search import search, search_with_provenance


class FakeDDGS:
    script: ClassVar[dict] = {}
    calls: ClassVar[list] = []

    def __init__(self, *a, **kw):
        pass

    def text(self, query, max_results=5, **kw):
        backend = kw.get("backend")
        FakeDDGS.calls.append(backend)
        outcome = FakeDDGS.script.get(backend)
        if outcome is None:
            return []
        kind, payload = outcome
        if kind == "err":
            raise RuntimeError(payload)
        return payload


def _results(*titles):
    return [{"title": t, "href": f"https://{t}.example/", "body": f"b{t}"} for t in titles]


@pytest.fixture
def fake(monkeypatch, tmp_path):
    FakeDDGS.script = {}
    FakeDDGS.calls = []
    monkeypatch.setattr("ddgs.DDGS", FakeDDGS)
    # Isolate the engine-health ledger; see the note in test_search_failover.
    monkeypatch.setenv("WEBGET_ENGINE_HEALTH", str(tmp_path / "engine_health.json"))
    return FakeDDGS


class TestPlainSearchStaysUnchanged:
    """Backward compatibility: search() must keep returning a bare list."""

    def test_search_returns_list_not_tuple(self, fake):
        fake.script["brave"] = ("ok", _results("a"))
        out = search("q", engine="brave")
        assert isinstance(out, list)
        assert out[0]["url"] == "https://a.example/"


class TestProvenanceOnSuccess:
    def test_reports_requested_engine_when_it_works(self, fake):
        fake.script["brave"] = ("ok", _results("a"))
        _res, prov = search_with_provenance("q", engine="brave")
        assert prov["engine"] == "brave"
        assert prov["requested"] == "brave"
        assert prov["failed_over"] is False

    def test_auto_is_reported_as_auto(self, fake):
        fake.script[None] = ("ok", _results("a"))
        _res, prov = search_with_provenance("q")
        assert prov["engine"] == "auto"
        assert prov["failed_over"] is False


class TestProvenanceOnFailover:
    def test_records_the_engine_that_actually_answered(self, fake):
        fake.script["google"] = ("err", "No results found.")
        fake.script["brave"] = ("ok", _results("b"))
        _res, prov = search_with_provenance("q", engine="google")
        assert prov["requested"] == "google"
        assert prov["engine"] == "brave"
        assert prov["failed_over"] is True

    def test_records_why_the_first_engine_failed(self, fake):
        fake.script["google"] = ("err", "No results found.")
        fake.script["brave"] = ("ok", _results("b"))
        _, prov = search_with_provenance("q", engine="google")
        assert "No results found" in prov["first_error"]

    def test_failed_over_when_first_engine_returned_empty(self, fake):
        fake.script["google"] = ("ok", [])
        fake.script["brave"] = ("ok", _results("b"))
        _, prov = search_with_provenance("q", engine="google")
        assert prov["failed_over"] is True
        assert prov["engine"] == "brave"


class TestProvenanceOnTotalFailure:
    def test_provenance_is_returned_even_when_everything_fails(self, fake):
        """The caller needs to know WHICH engines were tried and why."""
        fake.script["brave"] = ("err", "TLS handshake failed")
        for eng in ("google", "duckduckgo", "mojeek", "startpage", "wikipedia", "yahoo"):
            fake.script[eng] = ("err", "No results found.")
        with pytest.raises(RuntimeError) as ei:
            search_with_provenance("q", engine="brave")
        prov = getattr(ei.value, "provenance", None)
        assert prov is not None
        assert prov["engine"] is None
        assert "brave" in prov["tried"]

    def test_total_failure_reports_failed_over_when_others_were_tried(self, fake):
        """If alternatives were attempted, failed_over must be True.

        Regression: 'everything failed' used to report failed_over=False
        because the flag was derived from the answering engine only, so a
        caller could not tell that failover had even been attempted.
        """
        fake.script["brave"] = ("err", "TLS handshake failed")
        fake.script["google"] = ("ok", [])
        fake.script["duckduckgo"] = ("ok", [])
        with pytest.raises(RuntimeError) as ei:
            search_with_provenance("q", engine="brave")
        prov = ei.value.provenance
        assert prov["engine"] is None
        assert prov["failed_over"] is True
        assert len(prov["tried"]) > 1

    def test_single_engine_failure_with_no_alternatives_is_not_failed_over(self, fake):
        """failed_over means 'we used something else', not merely 'we errored'."""
        fake.script["brave"] = ("err", "boom")
        # Empty registry => nothing to fail over to.
        # NB: `from webget import search` yields the FUNCTION (the package
        # re-exports it), so reach the module through sys.modules.
        import sys

        search_mod = sys.modules["webget.search"]

        mp = pytest.MonkeyPatch()
        mp.setattr(search_mod, "known_engines", list)
        try:
            with pytest.raises(RuntimeError) as ei:
                search_with_provenance("q", engine="brave")
            assert ei.value.provenance["failed_over"] is False
        finally:
            mp.undo()

    def test_empty_everywhere_is_not_an_error_but_reports_no_engine(self, fake):
        res, prov = search_with_provenance("q", engine="brave")
        assert res == []
        assert prov["engine"] is None
