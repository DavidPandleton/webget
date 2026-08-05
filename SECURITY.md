# Security Policy

## Reporting a vulnerability

webget is a personal tool, but security issues are still taken seriously.

- **For sensitive reports:** email [tarigansdavid@gmail.com](mailto:tarigansdavid@gmail.com)
  with a subject like `[webget security] ...`. Do not include cookies or
  live session tokens in the report.
- **For everything else:** open an issue with the `bug` label.

## What webget does about auth data

- Cookies and session tokens are **never printed** to stdout or included in
  `--json` output.
- Persistent profiles live in `~/.local/share/webget/profiles/<name>` -
  outside the repo, ignored by git.
- Cache stores fetched content as plaintext JSON in `~/.cache/webget/`.
  If you fetch authenticated pages, use `--no-cache` to avoid persisting
  the results locally.

## Scope

The following are out of scope by design (not vulnerabilities):

- CAPTCHA solving or challenge bypass
- Fingerprint spoofing / stealth
- Circumventing access controls

## Supported versions

Only the latest release on `main` is supported. Backports happen on request.
