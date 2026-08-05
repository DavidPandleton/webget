# Contributing to webget

Thanks for stopping by. webget is a small tool, so the bar to contribute is
also small. A bug report with a clear reproduction beats a guess.

## Getting started

```bash
# clone + dev deps
git clone https://github.com/DavidPandleton/webget.git
cd webget
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# run tests
pytest

# lint
ruff check webget.py tests
```

Runtime deps (crawl4ai, ddgs, httpx, trafilatura, html2text) are only needed
when you actually run fetches. The test suite covers pure logic and does not
require them.

## What kind of PRs are welcome

- Bug fixes with a test
- New fetch strategies / auth-state signals
- Documentation and examples
- CI or packaging improvements

## What this project will not do

webget deliberately does **not** implement CAPTCHA solving, fingerprint
spoofing, stealth plugins, or anti-bot evasion. PRs that add those will be
closed. The tool's job is to report honestly what it hit
(`login_required`, `challenge`, `blocked`), not to sneak past walls.

## Security

- Never commit cookies, session tokens, or `storage_state` files.
- If a PR touches auth handling, add a test showing secrets do not leak into
  output.
- Sensitive issues? Email tarigansdavid@gmail.com or open a private report.

## Before opening a PR

1. `pytest` passes.
2. `ruff check webget.py tests` passes.
3. Smoke test your change: `webget s "test"` and
   `webget fetch https://example.com --json`.
4. Grep your diff for accidental secrets: `git diff | grep -iE "cookie|token|key="`.
