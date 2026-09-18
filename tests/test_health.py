"""Tests for the per-install engine health ledger (webget/health.py).

Contract being pinned:

  * health is ADVISORY. Any failure in reading, writing or scoring must
    degrade to "registry order", never to an exception. A health system that
    can break a search is worse than no health system.
  * scores are bounded [0, 1], unseen engines sit mid-range, stale entries
    are treated as unseen.
  * ordering is stable: equal scores keep the caller's order.
  * persistence goes to WEBGET_ENGINE_HEALTH and nowhere else.
"""

from __future__ import annotations

import json

import pytest

from webget import health


@pytest.fixture()
def ledger_file(tmp_path, monkeypatch):
    path = tmp_path / "state" / "engine_health.json"
    monkeypatch.setenv("WEBGET_ENGINE_HEALTH", str(path))
    return path


class TestRecord:
    def test_creates_entry_and_persists(self, ledger_file):
        health.record("brave", True, 0.2)
        data = json.loads(ledger_file.read_text())
        assert data["brave"]["n"] == 1
        assert data["brave"]["ok"] == pytest.approx(1.0)

    def test_ewma_makes_repeated_failures_drop_the_score(self, ledger_file):
        for _ in range(8):
            health.record("brave", False, 0.0)
        data = json.loads(ledger_file.read_text())
        assert data["brave"]["ok"] < 0.1

    def test_auto_is_never_recorded(self, ledger_file):
        health.record("auto", True, 0.1)
        assert not ledger_file.exists()

    def test_empty_name_is_ignored(self, ledger_file):
        health.record("", True, 0.1)
        health.record(None, True, 0.1)
        assert not ledger_file.exists()


class TestScore:
    def test_unseen_is_mid_range(self):
        assert health.score(None) == health.UNSEEN_SCORE
        assert health.score({}) == health.UNSEEN_SCORE

    def test_stale_entry_scores_as_unseen(self):
        import time

        entry = {"ok": 0.0, "n": 5, "lat": 0.0,
                 "ts": time.time() - health.STALE_AFTER_S - 10}
        assert health.score(entry) == health.UNSEEN_SCORE

    def test_score_is_bounded(self):
        entry = {"ok": 1.0, "n": 5, "lat": 99.0, "ts": __import__("time").time()}
        assert 0.0 <= health.score(entry) <= 1.0

    def test_fast_engine_outranks_slow_engine_at_same_success(self):
        import time

        now = time.time()
        fast = {"ok": 1.0, "n": 5, "lat": 0.1, "ts": now}
        slow = {"ok": 1.0, "n": 5, "lat": 8.0, "ts": now}
        assert health.score(fast) > health.score(slow)


class TestOrder:
    def test_orders_best_first(self, ledger_file):
        import time

        now = time.time()
        ledger_file.parent.mkdir(parents=True)
        ledger_file.write_text(json.dumps({
            "a": {"ok": 1.0, "n": 9, "lat": 0.1, "ts": now},
            "b": {"ok": 0.0, "n": 9, "lat": 0.1, "ts": now},
            "c": {"ok": 0.9, "n": 9, "lat": 0.1, "ts": now},
        }))
        out = health.order(["b", "c", "a"])
        assert out[0] == "a" and out[-1] == "b"

    def test_never_drops_a_candidate(self, ledger_file):
        out = health.order(["x", "y", "z"])
        assert sorted(out) == ["x", "y", "z"]

    def test_equal_scores_keep_caller_order(self, ledger_file):
        # No ledger file at all: everyone is unseen, order must be preserved.
        out = health.order(["m", "n", "p"])
        assert out == ["m", "n", "p"]

    def test_garbage_ledger_degrades_to_input_order(self, ledger_file, monkeypatch):
        ledger_file.parent.mkdir(parents=True)
        ledger_file.write_text("{not json at all")
        assert health.order(["b", "a"]) == ["b", "a"]

    def test_unwritable_ledger_does_not_raise(self, monkeypatch):
        monkeypatch.setenv("WEBGET_ENGINE_HEALTH", "/proc/no/such/dir/h.json")
        # record() must swallow the OSError, not propagate it.
        health.record("brave", True, 0.1)  # must not raise
        # and order() still works with a load that returns {}
        assert health.order(["a", "b"]) == ["a", "b"]


class TestSnapshot:
    def test_snapshot_shape(self, ledger_file):
        health.record("brave", True, 0.3)
        snap = health.health_snapshot()
        e = snap["brave"]
        assert {"score", "success_rate", "avg_latency_s", "observations",
                "age_s", "stale"} <= set(e)
        assert e["stale"] is False
        assert e["observations"] == 1

    def test_snapshot_on_missing_file(self, ledger_file):
        assert health.health_snapshot() == {}
