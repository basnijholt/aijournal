# aijournal

Local-first, YAML-centric personal self-modeling agent. All authoritative data lives in human-readable files; derived artifacts are reproducible via local Ollama. See `PLAN.md` for end-to-end specs, schemas, flows, and commit roadmap.

## Getting Started

```sh
uv sync
uv run pytest -q
```

Key directories will be created by `aijournal init` in future commits. For now, see `config/` for defaults and `profile/` for the seeded self profile and claims scaffold.

## Pre-commit Hooks

Install [pre-commit](https://pre-commit.com/) once, then enable the hooks locally:

```sh
pipx run pre-commit install
```

To dry-run the hooks across the repo without touching staged files:

```sh
just precommit_dry
```

To run the full hook suite before pushing:

```sh
just precommit
```
