"""Tests for engine provenance surfacing through the CLI.

The point of provenance is that it must be REACHABLE. A stderr warning is
lost when output is piped, so `webget s q --json` has to carry it in the
payload. Note the bare `webget s q --json` path did not previously exist at
all (cmd == "s" had no json branch), so this adds it.
"""

from __future__ import annotations

import json

from webget import cli


def _run(argv, monkeypatch, capsys, search_impl=None, prov_impl=None):
    """Run the CLI with search stubbed.

    Both hooks are patched: the CLI prefers `search_with_provenance`, so
    patching only `search` would leave the real network path live. Pass
    `prov_impl` to control provenance explicitly.
    """
    impl = search_impl or (lambda *a, **k: [])

    if prov_impl is None:

        def _default_prov(query, n=5, engine=None):
            return impl(query, n, engine), {
                "requested": engine or "auto",
                "engine": engine or "auto",
                "failed_over": False,
            }

        prov_impl = _default_prov

    monkeypatch.setattr(cli, "search", lambda *a, **k: impl(*a, **k))
    monkeypatch.setattr(cli, "search_with_provenance", prov_impl)
    monkeypatch.setattr("sys.argv", ["webget", *argv])
    try:
        cli.main()
    except SystemExit as e:
        assert e.code in (0, None)
    return capsys.readouterr()


class TestProvenanceIsWiredUp:
    """Guard against the fallback silently swallowing real provenance.

    `_search_with_prov` falls back to a best-effort dict when
    `search_with_provenance` is missing from the module namespace. That
    fallback exists for older tests, but if the real import is ever
    dropped the CLI would report `failed_over: False` forever while
    looking perfectly green. This asserts the real function is reachable.
    """

    def test_cli_namespace_exposes_the_real_function(self):
        assert hasattr(cli, "search_with_provenance")


class TestSearchJsonOutput:
    def test_json_flag_emits_valid_json(self, monkeypatch, capsys):
        out = _run(
            ["s", "q", "--json"],
            monkeypatch,
            capsys,
            lambda *a, **k: [{"title": "T", "url": "https://x.example/", "snippet": "s"}],
        )
        payload = json.loads(out.out)
        assert payload["results"][0]["url"] == "https://x.example/"

    def test_json_includes_engine_provenance_when_available(self, monkeypatch, capsys):
        def fake_prov(query, n=5, engine=None):
            return (
                [{"title": "T", "url": "https://x.example/", "snippet": "s"}],
                {
                    "requested": "google",
                    "engine": "brave",
                    "failed_over": True,
                    "tried": ["google", "brave"],
                    "first_error": "No results found.",
                },
            )

        out = _run(["s", "q", "--json"], monkeypatch, capsys, prov_impl=fake_prov)
        payload = json.loads(out.out)
        assert payload["engine"] == "brave"
        assert payload["requested_engine"] == "google"
        assert payload["failed_over"] is True

    def test_plain_output_still_prints_results(self, monkeypatch, capsys):
        out = _run(
            ["s", "q"],
            monkeypatch,
            capsys,
            lambda *a, **k: [{"title": "T", "url": "https://x.example/", "snippet": "snip"}],
        )
        assert "https://x.example/" in out.out
        assert "1." in out.out
