# Webget - quick developer commands

.PHONY: install dev test lint fmt clean

install:
	uv pip install --python $$(which python3) crawl4ai ddgs httpx trafilatura html2text
	@echo "done. run: cp webget.py ~/.local/bin/webget && chmod +x ~/.local/bin/webget"

dev:
	uv pip install --python $$(which python3) -e ".[dev]"

test:
	python3 -m pytest

lint:
	python3 -m ruff check webget.py tests

fmt:
	python3 -m ruff format webget.py tests

clean:
	rm -rf __pycache__ .pytest_cache .ruff_cache
