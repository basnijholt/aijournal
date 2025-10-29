# Contributing to aijournal

Thanks for your interest in improving `aijournal`! This guide describes how to set up a development environment, run tests, and follow project conventions so changes integrate smoothly.

## 1. Environment Setup

1. **Install prerequisites**
   - Python 3.11+
   - [`uv`](https://docs.astral.sh/uv/) for dependency and virtualenv management

2. **Clone the repository and install dependencies**
   ```bash
   git clone https://github.com/basnijholt/aijournal.git
   cd aijournal
   uv sync
   ```

3. **Run the test suite to confirm the environment**
   ```bash
   uv run pytest -q
   ```

4. **Optional:** install the local pre-commit hooks so Ruff, Ruff-format, and mypy run automatically before each commit.
   ```bash
   uvx pre-commit install
   ```

5. **Recommended:** enable the bundled git hooks (includes a schema check and full test run before `git push`).
   ```bash
   git config core.hooksPath .githooks
   ```

## 2. Working with uv

- Use `uv run <command>` to execute anything inside the project environment (e.g., `uv run aijournal summarize ...`, `uv run pytest`).
- Add or remove dependencies with `uv add` / `uv remove`; commit both `pyproject.toml` and `uv.lock` after changes.
- The `justfile` in the repo contains helpful shortcuts (`uv run just test`, `just fmt`, etc.), but `uv` remains the single source of truth.

## 3. Tests and Quality Gates

- **Unit tests:** `uv run pytest -q`
- **Coverage (optional):** `uv run pytest --cov=src -q`
- **Static analysis:** `uv run mypy src`
- **Linting / formatting:** `uv run ruff check src tests` and `uv run ruff format src tests`

Please run the test suite and at least the Ruff formatter before submitting a PR. CI enforces the same checks. When data-model structures change, regenerate schemas via:
```bash
uv run python scripts/check_schemas.py --bless
uv run python scripts/check_schemas.py  # should report no changes
```

## 4. Fake vs. Live Mode

- Set `AIJOURNAL_FAKE_OLLAMA=1` to run deterministic fixtures during tests and local development. This avoids hitting a real Ollama server.
- Live mode targets a remote Ollama instance (see `ARCHITECTURE.md` and `agents.md` for host details). Never export `AIJOURNAL_FAKE_OLLAMA=1` when validating live-mode behaviour.

## 5. Commit Conventions

- Keep commits focused and descriptive. Prefixes like `feat`, `fix`, `docs`, or `chore` are encouraged but not strictly required.
- Never rewrite history on `main`. If you need to fix a commit, add a new one.
- Include relevant test updates alongside code changes whenever behaviour shifts.

## 6. Filing Issues and Pull Requests

- Open an issue for significant feature work or architectural changes before submitting a pull request.
- PRs should link back to the corresponding issue (if any) and include a short summary of the change plus testing notes.
- Ensure documentation (README, workflow guide, architecture doc) stays accurate when behaviour changes.

Following this workflow keeps the project reproducible and easy to reason about for both humans and automated agents. Thank you for contributing!
