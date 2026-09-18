"""Tests for automatic engine failover in the search layer.

Design decisions these tests pin down (chosen when the user did not reply,
kept deliberately conservative and easy to change):

  1. A search that SUCCEEDS on the first engine makes exactly ONE call.
     No speculative fan-out: latency is a feature.
  2. A search that FAILS (raises) tries other engines, in order, and
     returns the first non-empty result.
  3. Failover is ANNOUNCED on stderr. Silent failover would be a lie: the
     user asked for --engine brave and would receive google's results with
     no hint. Visibility is the whole point.
  4. If every engine fails, raise the ORIGINAL error rather than a generic
     "all failed" one, so the real cause (TLS, 429, whatever) survives.
  5. An explicit engine is still tried FIRST. Failover is a safety net,
     never an override of a working request.

These run against a fake DDGS so they are deterministic and offline.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

import webget.search as search_mod  # noqa: F401 - module, used for known_engines
from webget.search import MAX_FAILOVER_ATTEMPTS, known_engines, search


class FakeDDGS:
    """Scripted DDGS stand-in: backend -> ('ok', [...]) or ('err', msg)."""

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
    return [{"title": t, "href": f"https://{t}.example/", "body": f"body {t}"} for t in titles]


@pytest.fixture
def fake(monkeypatch):
    FakeDDGS.script = {}
    FakeDDGS.calls = []
    monkeypatch.setattr("ddgs.DDGS", FakeDDGS)
    return FakeDDGS


class TestNoFailoverOnSuccess:
    def test_single_engine_success_makes_one_call(self, fake):
        fake.script["brave"] = ("ok", _results("a"))
        out = search("q", engine="brave")
        assert out[0]["url"] == "https://a.example/"
        assert fake.calls == ["brave"]

    def test_success_is_not_retried_on_another_engine(self, fake):
        fake.script["brave"] = ("ok", _results("a"))
        fake.script["google"] = ("ok", _results("b"))
        search("q", engine="brave")
        assert "google" not in fake.calls


class TestFailoverOnError:
    def test_falls_back_to_another_engine_when_first_raises(self, fake):
        fake.script["brave"] = ("err", "boom")
        fake.script["google"] = ("ok", _results("g"))
        out = search("q", engine="brave")
        assert out[0]["url"] == "https://g.example/"
        assert fake.calls[0] == "brave"
        assert "google" in fake.calls

    def test_failover_is_announced_on_stderr(self, fake, capsys):
        fake.script["brave"] = ("err", "boom")
        fake.script["google"] = ("ok", _results("g"))
        search("q", engine="brave")
        err = capsys.readouterr().err
        assert "brave" in err
        assert "warning" in err.lower()


class TestAllEnginesFail:
    def test_raises_original_error_when_everything_fails(self, fake):
        fake.script["brave"] = ("err", "TLS handshake failed")
        fake.script["google"] = ("err", "No results found.")
        fake.script["duckduckgo"] = ("err", "No results found.")
        with pytest.raises(RuntimeError, match="TLS handshake failed"):
            search("q", engine="brave")


class TestAutoAlsoFailsOver:
    """'auto' failing is the real-world case from the benchmark.

    ddgs's auto aggregation can fail as a whole (every engine it tries is
    down for this host). That is exactly when a second attempt at a known
    single engine is worth making.
    """

    def test_auto_failure_falls_back_to_a_named_engine(self, fake):
        fake.script[None] = ("err", "all upstream engines failed")
        fake.script["brave"] = ("ok", _results("b"))
        out = search("q")  # no engine arg -> ddgs default (auto)
        assert out and out[0]["url"] == "https://b.example/"

    def test_auto_failure_with_no_working_engine_reraises(self, fake):
        fake.script[None] = ("err", "all upstream engines failed")
        with pytest.raises(RuntimeError, match="all upstream engines failed"):
            search("q")


class TestEmptyResultIsNotSuccess:
    """A no-exception-but-zero-results call is a soft failure.

    ddgs returns [] for some blocked engines instead of raising, so an
    empty list must also trigger failover or the block is invisible.
    """

    def test_empty_first_engine_falls_over(self, fake):
        fake.script["brave"] = ("ok", [])
        fake.script["google"] = ("ok", _results("g"))
        out = search("q", engine="brave")
        assert out and out[0]["url"] == "https://g.example/"

    def test_empty_first_engine_is_announced(self, fake, capsys):
        fake.script["brave"] = ("ok", [])
        fake.script["google"] = ("ok", _results("g"))
        search("q", engine="brave")
        assert "brave" in capsys.readouterr().err

    def test_all_empty_returns_empty_not_error(self, fake):
        out = search("q", engine="brave")
        assert out == []


class TestFailoverIsBounded:
    """Failover must not turn one slow query into N*timeout.

    An agent calling search in a loop cannot afford an unbounded chain of
    dead engines. The chain is capped so worst-case latency stays close to
    the single-engine case.
    """

    def test_chain_is_capped(self, fake):
        # Every engine raises. Count how many were actually attempted.
        for eng in known_engines():
            fake.script[eng] = ("err", "down")
        with pytest.raises(RuntimeError):
            search("q", engine="brave")
        # 1 (the requested engine) + the cap, never the whole registry.
        assert len(fake.calls) <= MAX_FAILOVER_ATTEMPTS + 1

    def test_cap_constant_is_small(self):
        assert 1 <= MAX_FAILOVER_ATTEMPTS <= 4
