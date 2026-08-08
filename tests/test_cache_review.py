"""Phase 11 cache atomicity + durability review tests."""

import json
import os
import threading

import webget_cli as webget


class TestAtomicDurability:
    def test_write_is_atomic_tmp_rename(self, isolated_env, monkeypatch):
        """cache_put must go through tmp + os.replace (spy on os.replace)."""
        url = "https://atomic.test/x"
        real_replace = os.replace
        calls = []

        def spy(src, dst):
            calls.append((src, dst))
            return real_replace(src, dst)

        monkeypatch.setattr(os, "replace", spy)
        webget.cache_put(url, None, None, 1000, {"status": "success"}, None)
        assert calls and calls[0][0].endswith(".tmp")
        assert calls[0][1] == webget._cache_path(url, None, None, 1000, None)

    def test_no_tmp_leftovers(self, isolated_env):
        url = "https://atomic.test/clean"
        webget.cache_put(url, None, None, 1000, {"status": "success"}, None)
        cache = isolated_env["cache"]
        leftovers = [f for f in os.listdir(cache) if f.endswith(".tmp")]
        assert leftovers == []

    def test_read_while_write_never_sees_partial(self, isolated_env):
        """Concurrent readers must always parse a complete file."""
        url = "https://atomic.test/race"
        errors = []
        stop = threading.Event()

        def writer():
            for i in range(30):
                webget.cache_put(
                    url,
                    None,
                    None,
                    1000,
                    {"status": "success", "payload": "x" * 10000, "i": i},
                    None,
                )
            stop.set()

        def reader():
            while not stop.is_set():
                data = webget.cache_get(url, None, None, 1000, 3600)
                if data is not None:
                    try:
                        assert data["payload"] == "x" * 10000
                    except AssertionError as e:
                        errors.append(str(e))

        t1 = threading.Thread(target=writer)
        t2 = threading.Thread(target=reader)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        assert errors == []

    def test_corrupt_existing_cache_returns_none(self, isolated_env):
        p = webget._cache_path("https://c.test/1", None, None, 1000, None)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write("{truncated")
        assert webget.cache_get("https://c.test/1", None, None, 1000, 3600) is None

    def test_concurrent_writers_all_valid(self, isolated_env):
        url = "https://atomic.test/multi"
        results = []

        def writer(i):
            try:
                webget.cache_put(url, None, None, 1000, {"status": "success", "i": i}, None)
                results.append(i)
            except Exception as e:  # noqa: BLE001
                results.append(("ERR", str(e)))

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(15)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not [r for r in results if isinstance(r, tuple)]
        with open(webget._cache_path(url, None, None, 1000, None)) as f:
            json.load(f)  # must parse

    def test_write_json_atomic_for_logout(self, isolated_env):
        """_write_json (logout path) must also be tmp+rename."""
        p = os.path.join(isolated_env["profiles"], "state.json")
        real_replace = os.replace
        calls = []
        orig = os.replace

        def spy(src, dst):
            calls.append((src, dst))
            return orig(src, dst)

        os.replace = spy
        try:
            webget._write_json(p, {"cookies": []})
        finally:
            os.replace = real_replace
        assert calls and calls[0][0].endswith(".tmp")
        assert os.path.exists(p)
        with open(p) as f:
            assert json.load(f) == {"cookies": []}
