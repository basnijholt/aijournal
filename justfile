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
    AIJOURNAL_FAKE_OLLAMA=1 uv run pytest -q && uv run mypy src

precommit_dry:
    pre-commit run --all-files --config .pre-commit-config.yaml

precommit:
    pre-commit run --all-files --config .pre-commit-config.yaml
