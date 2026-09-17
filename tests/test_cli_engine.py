"""Tests for the CLI surface of multi-engine search."""

from webget import cli


def _opts(*args):
    return cli.parse_opts(list(args))


class TestParseOptsEngine:
    def test_engine_flag_long(self):
        r = _opts("s", "q", "--engine", "brave")
        assert r[-1] == "brave"

    def test_engine_flag_short(self):
        r = _opts("s", "q", "-e", "brave,duckduckgo")
        assert r[-1] == "brave,duckduckgo"

    def test_engine_defaults_to_none(self):
        r = _opts("s", "q")
        assert r[-1] is None


class TestCmdSearchEngine:
    def test_engine_reaches_search_call(self, monkeypatch, capsys):
        captured = {}

        def fake_search(query, n=5, engine=None):
            captured["query"] = query
            captured["n"] = n
            captured["engine"] = engine
            return [{"title": "T", "url": "https://x.example", "snippet": "s"}]

        # patch where it is USED (webget.cli namespace), not where defined
        monkeypatch.setattr(cli, "search", fake_search)
        monkeypatch.setattr("sys.argv", ["webget", "s", "query", "--engine", "brave"])
        try:
            cli.main()
        except SystemExit as e:
            assert e.code in (0, None)
        assert captured["engine"] == "brave"
        assert captured["query"] == "query"

    def test_invalid_engine_does_not_crash_cli(self, monkeypatch, capsys):
        monkeypatch.setattr(
            cli,
            "search",
            lambda query, n=5, engine=None: [
                {"title": "T", "url": "https://y.example", "snippet": "s"}
            ],
        )
        monkeypatch.setattr("sys.argv", ["webget", "s", "q", "--engine", "totally-bogus"])
        try:
            cli.main()
        except SystemExit as e:
            assert e.code in (0, None)
