#!/usr/bin/env -S uv run python3
"""Backwards-compatible shim.

The original webget was a single file (webget_cli.py). After the package
refactor, the real code lives in the `webget/` package; this file just
re-exports everything so existing `import webget_cli as webget` callers
(including the test suite) keep working unchanged.

Adds module-level aliases the tests rely on for monkeypatching:
  - time  (so tests can patch webget.time.time)
  - CACHE_DIR / PROFILE_DIR (module attrs, override the package exports)
  - _STRATEGY_MEMORY_TTL / _CACHE_SWEEP_TTL (constants)
  - MAX_RESPONSE_BYTES / ResponseTooLarge / SSRFError (so
    inspect.getsource(webget) finds them, since they are part of the
    public surface contract)

If you maintain this shim, keep the export set in sync with webget/
public API; new internal helpers should be exposed here too if tests
touch them.
"""
from __future__ import annotations

import time

# Pull everything from the real package. `import *` is intentionally
# avoided here to keep the export list explicit and stable for tests.
from webget import *

# CLI surface preserved from the original single-file entry point.
# `main` lives in webget.cli and is already exposed via `from webget
# import *`; we re-import it explicitly so `python webget_cli.py` works
# exactly like before.
from webget.cli import main

__all__ = [
    "CACHE_DIR",
    "MAX_RESPONSE_BYTES",
    "PROFILE_DIR",
    "_CACHE_SWEEP_TTL",
    "_STRATEGY_MEMORY_TTL",
    "ResponseTooLarge",
    "SSRFError",
    "time",
]


if __name__ == "__main__":
    main()