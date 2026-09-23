import io
import json
import sys
from contextlib import redirect_stdout

import pytest

from webget import cli


def test_cli_crawl_dispatches_and_emits_json(monkeypatch, tmp_path):
    seen = {}

    async def fake_crawl(seed, frontier, **kwargs):
        seen.update(seed=seed, frontier=frontier, kwargs=kwargs)
        return {"stats": {"done": 1, "failed": 0}, "results": []}

    monkeypatch.setattr(cli, "crawl_site", fake_crawl)
    monkeypatch.setattr(
        sys,
        "argv",
        ["webget", "crawl", "https://example.com", str(tmp_path / "crawl.db"), "--json", "--limit", "1"],
    )
    output = io.StringIO()
    with redirect_stdout(output):
        cli.main()
    assert json.loads(output.getvalue())["stats"]["done"] == 1
    assert seen["seed"] == "https://example.com"
    assert seen["kwargs"]["max_pages"] == 1


def test_cli_crawl_requires_frontier_path(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["webget", "crawl", "https://example.com"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    captured = capsys.readouterr()
    assert exc.value.code == 2
    assert "crawl expects URL and frontier SQLite path" in captured.out
