.PHONY: install lint format test check

install:
	uv sync

lint:
	uv run ruff check .

format:
	uv run ruff format .

test:
	uv run pytest

check:
	uv run ruff check .
	uv run ruff format --check .
	uv run pytest