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

# NOTE: `import webget.search as sm` does NOT give the module here - the
# package re-exports the `search` function, so the name resolves to a function
# and attribute access like search_mod.FAILOVER_BUDGET_S raises AttributeError. Use
# importlib when the module itself is needed.
import importlib as _importlib
from typing import ClassVar

import pytest

from webget.search import known_engines, search

search_mod = _importlib.import_module("webget.search")


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
def fake(monkeypatch, tmp_path):
    FakeDDGS.script = {}
    FakeDDGS.calls = []
    monkeypatch.setattr("ddgs.DDGS", FakeDDGS)
    # Isolate the engine-health ledger. Without this the tests write real
    # observations into ~/.local/state/webget/engine_health.json, and because
    # the ledger reorders the failover chain, test outcomes start depending on
    # whatever the last run left behind: the same suite passed and failed on
    # an unchanged tree. Tests must not mutate user state, and they must not
    # read it either.
    monkeypatch.setenv("WEBGET_ENGINE_HEALTH", str(tmp_path / "engine_health.json"))
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
    dead engines. The bound is a TIME budget, not a count of attempts: a count
    is either too many (3 x 20s timeout = 60s) or too few (the working engine
    sat at position 5). These tests pin the time semantics down.
    """

    def test_chain_is_capped_by_attempts_when_engines_are_instant(self, fake):
        # Every engine raises, and instantly. The time budget will not expire,
        # so this must terminate because the chain ran out, not loop forever.
        for eng in known_engines():
            fake.script[eng] = ("err", "down")
        with pytest.raises(RuntimeError):
            search("q", engine="brave")
        # The requested engine plus at most one pass over the registry.
        assert 1 <= len(fake.calls) <= len(known_engines()) + 1

    def test_deadline_stops_the_walk(self, fake, monkeypatch):
        """With a zero budget, only MIN_FAILOVER_ATTEMPTS alternates are tried.

        A budget of 0 combined with instant failures isolates the deadline
        logic from network timing: if the deadline were ignored, the walk
        would continue through all 8 remaining engines.
        """
        monkeypatch.setenv("WEBGET_FAILOVER_BUDGET_S", "0")
        for eng in known_engines():
            fake.script[eng] = ("err", "down")
        with pytest.raises(RuntimeError) as ei:
            search("q", engine="brave")
        # requested + minimum alternates, and nowhere near the full registry.
        assert len(fake.calls) == 1 + search_mod.MIN_FAILOVER_ATTEMPTS
        assert ei.value.provenance["budget_exhausted"] is True
        assert ei.value.provenance["untried"] > 0

    def test_reaches_an_engine_past_the_old_count_cap(self, fake):
        """The bug this replaced: a working engine at position 5 was unreachable.

        With a fixed cap of 3 alternates, only positions 1-3 were ever tried.
        A time budget makes the reach depend on how fast the dead ones fail,
        not on an arbitrary number.
        """
        engines = known_engines()
        alive = engines[4]  # the fifth alternate
        for eng in engines:
            fake.script[eng] = ("err", "down")
        fake.script[alive] = ("ok", _results("alive"))
        out = search("q", engine=engines[0])
        assert out and out[0]["url"] == "https://alive.example/"
        assert alive in fake.calls

    def test_budget_starts_after_the_requested_engine(self, fake, monkeypatch):
        """A slow first attempt must not consume the failover budget.

        If the deadline included the requested engine's own call, one slow
        engine would leave the chain unbudgeted and silently disable the
        safety net. With a tiny budget and a first engine that overruns it,
        failover must still reach a working alternate.
        """
        import time as _t

        calls = []

        class SlowFirst:
            def __init__(self, *a, **kw):
                pass

            def text(self, query, max_results=5, **kw):
                backend = kw.get("backend")
                calls.append(backend)
                if backend == "brave":
                    _t.sleep(0.4)  # overruns the 0.2s budget below
                    raise RuntimeError("slow first engine")
                return _results("z")

        monkeypatch.setenv("WEBGET_FAILOVER_BUDGET_S", "0.2")
        monkeypatch.setattr("ddgs.DDGS", SlowFirst)

        out = search("q", engine="brave")
        assert out and out[0]["url"] == "https://z.example/"
        assert calls[0] == "brave" and len(calls) >= 2

    def test_budget_constant_is_sane(self):
        assert 1.0 <= search_mod.FAILOVER_BUDGET_S <= 30.0
        assert 1 <= search_mod.MIN_FAILOVER_ATTEMPTS <= 3
