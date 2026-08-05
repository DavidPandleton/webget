# Webget - quick developer commands

.PHONY: install dev test lint fmt clean

install:
	uv pip install --python $$(which python3) .
	@echo "done. binary available via entry point 'webget'"

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
