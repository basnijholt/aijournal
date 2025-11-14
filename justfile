set shell := ["/bin/sh", "-c"]

default:
    @just --help

test:
    uv run pytest -q

test_cov:
    uv run pytest --cov=src -q

mypy:
    uv run mypy src

lint:
    uv run ruff check src tests

fmt:
    uv run ruff format src tests

health:
    uv run aijournal ollama health

fake_on:
    echo "export AIJOURNAL_FAKE_OLLAMA=1"

ci:
    uv run pytest -q && uv run mypy src && uv run python scripts/check_schemas.py

precommit_dry:
    uvx pre-commit run --all-files --show-diff-on-failure

precommit:
    uvx pre-commit run --all-files
