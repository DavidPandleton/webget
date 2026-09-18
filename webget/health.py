"""Per-install engine health ledger and adaptive fallback ordering.

Why this exists
---------------
0.13.0 fails over in a fixed order: whatever the ddgs registry happens to
list. The 0.13.0 benchmark showed that order is close to worst-case on a real
connection - on one host, 7 of 9 engines returned nothing, and the engine
that answered was rarely the one tried first. Every search therefore pays for
the dead engines it walks past before reaching a live one.

What this is NOT
----------------
It is not a baked-in ranking. The benchmark in data/search_bench/ is one
machine on one day; hardcoding it would ship that machine's ISP filtering to
every user, and the volatile-engine result in the report shows the ranking
changes within minutes. Health is learned per install, at runtime, and decays.

Design
------
  * `record(engine, ok, latency)` folds one observation into a score.
  * Score is an exponentially weighted success rate plus a latency penalty,
    so an engine that answers slowly ranks below one that answers fast.
  * A `stale` window discards old observations: an engine that was blocked
    last week is re-tried today with no penalty, which matters because the
    blocking is exactly what changes.
  * `order(candidates)` returns candidates sorted by score, with never-seen
    engines in the middle: not first (unproven) and not last (they may be the
    only live one, and a fresh install has nothing else).
  * Persistence is best-effort. A failure to read or write the ledger must
    never break a search, so every filesystem error is swallowed.

The ledger is advisory only. It cannot remove an engine from the candidate
set, only reorder it, so a wrong score costs latency and never correctness.
"""

from __future__ import annotations

import json
import os
import time

# Observations older than this are ignored when scoring. Chosen to match how
# fast the underlying blocking changes: the benchmark found the live-engine set
# shifting within minutes, so a week is already conservative. The point is that
# a stale verdict never permanently excludes a working engine.
STALE_AFTER_S = 7 * 24 * 3600

# Weight of a new observation in the EWMA. 0.3 means roughly the last 6-7
# observations dominate, which is a few queries - fast enough to react to an
# engine going down mid-session without flapping on one bad packet.
ALPHA = 0.3

# Latency penalty: score -= min(latency, CAP) / CAP * WEIGHT. An engine at or
# above the cap loses this much from its success rate, so a 100% engine that
# takes 8s ranks below a 100% engine that takes 1s, but still above a 50%
# engine. Without this, health sorting would prefer a reliable-but-glacial
# engine and make failover slower than the fixed order it replaced.
LATENCY_CAP_S = 8.0
LATENCY_WEIGHT = 0.25

# Where a never-seen engine sorts. 0.5 sits deliberately mid-range: an agent
# installing webget has no history, so it must still try unknown engines, and
# a proven-bad engine (score ~0) should rank below an unknown one.
UNSEEN_SCORE = 0.5


def _ledger_path():
    """Location of the health ledger, outside the cache dir.

    The cache dir is cleared by --fresh and by users; health is not cache data
    and should not be dropped when a cache entry is. Overridable for tests.
    """
    override = os.environ.get("WEBGET_ENGINE_HEALTH")
    if override:
        return override
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(base, "webget", "engine_health.json")


def _now():
    return time.time()


def load():
    """Read the ledger. Returns {} on any problem - health is best-effort."""
    try:
        with open(_ledger_path()) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(ledger):
    """Write the ledger atomically. Silent on failure by design."""
    path = _ledger_path()
    tmp = None
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = _tempfile_in(os.path.dirname(path))
        with os.fdopen(fd, "w") as f:
            json.dump(ledger, f)
        os.replace(tmp, path)
    except OSError:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


def _tempfile_in(directory):
    import tempfile

    return tempfile.mkstemp(dir=directory, prefix="engine_health.", suffix=".tmp")


def record(engine, ok, latency_s=0.0, ledger=None):
    """Fold one observation for `engine` into the ledger and persist it.

    Returns the updated ledger so a caller can batch: pass the same dict and
    only call save() once.
    """
    if not engine or engine == "auto":
        # "auto" is a request for the metasearch, not an engine, so it has no
        # place in a per-engine health ledger.
        return ledger if ledger is not None else load()

    led = load() if ledger is None else ledger
    entry = led.setdefault(engine, {"ok": None, "n": 0, "lat": 0.0, "ts": 0.0})

    prev = entry.get("ok")
    if prev is None:
        entry["ok"] = 1.0 if ok else 0.0
    else:
        entry["ok"] = (1 - ALPHA) * float(prev) + ALPHA * (1.0 if ok else 0.0)

    if ok and latency_s:
        prev_lat = float(entry.get("lat") or 0.0)
        # EWMA on latency too, so a single timeout does not dominate.
        entry["lat"] = latency_s if not prev_lat else (1 - ALPHA) * prev_lat + ALPHA * latency_s
    entry["n"] = int(entry.get("n") or 0) + 1
    entry["ts"] = _now()

    save(led)
    return led


def score(entry, now=None):
    """Score one ledger entry in [0, 1]. Higher is better."""
    if not entry:
        return UNSEEN_SCORE
    now = now or _now()
    if now - float(entry.get("ts") or 0) > STALE_AFTER_S:
        # Too old to be believed. Sorting it as unseen is the safe call: still
        # tried, not punished for a verdict that may no longer hold.
        return UNSEEN_SCORE
    ok = entry.get("ok")
    if ok is None:
        return UNSEEN_SCORE
    lat = min(float(entry.get("lat") or 0.0), LATENCY_CAP_S)
    return float(ok) - (lat / LATENCY_CAP_S) * LATENCY_WEIGHT


def order(candidates, ledger=None):
    """Return `candidates` sorted best-first by learned health.

    Stable for equal scores, so the caller's order is preserved among engines
    with no history. Never drops a candidate.
    """
    led = load() if ledger is None else ledger
    now = _now()

    def key(item):
        idx, name = item
        sc = score(led.get(name), now)
        # -idx as tiebreak keeps registry order for equal scores without
        # relying on sorts being stable across Python implementations.
        return (-sc, idx)

    return [name for _, name in sorted(enumerate(candidates), key=key)]


def health_snapshot(ledger=None):
    """Current scores, for diagnostics and tests."""
    led = load() if ledger is None else ledger
    now = _now()
    return {
        name: {
            "score": round(score(entry, now), 3),
            "success_rate": entry.get("ok"),
            "avg_latency_s": round(float(entry.get("lat") or 0.0), 2),
            "observations": entry.get("n", 0),
            "age_s": round(now - float(entry.get("ts") or 0), 1),
            "stale": (now - float(entry.get("ts") or 0)) > STALE_AFTER_S,
        }
        for name, entry in sorted(led.items())
    }
