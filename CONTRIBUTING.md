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

5. **Recommended:** enable the bundled git hooks (runs the schema check and full test suite before each `git push`).
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

Please run the test suite and at least the Ruff formatter before submitting a PR. CI enforces the same checks.

### Schema management

When data-model structures change, regenerate schemas and verify the clean run:

```bash
uv run python scripts/check_schemas.py --bless && uv run python scripts/check_schemas.py
```

Setting the environment variable is equivalent to passing `--bless`:

```bash
SCHEMAS_BLESS=1 uv run python scripts/check_schemas.py
```

The pre-push hook and CI workflows run the non-bless check automatically; remember to commit updated schema files alongside code changes.

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

## 7. LLM prompts and DTO contracts

Structured LLM commands follow a strict boundary so prompts stay stable and runtime models remain deterministic:

- Typer commands that call `_invoke_structured_llm` **must** set `response_model` to a prompt DTO such as `PromptProfileUpdates`, `PromptMicroFacts`, `PromptFacetItem`, etc., or to a small allowlist of DTOs (`DailySummary`, `AdviceCard`). Never point `response_model` at runtime artifacts like `ProfileUpdateProposals`, `MicroFactsFile`, `FacetChange`, or any `Artifact[...]`.
- Every new structured prompt needs a DTO defined in `src/aijournal/domain/prompts.py` (or a nearby domain module) plus a converter (e.g., `convert_prompt_microfacts`) that maps DTO instances into runtime models, adding IDs, provenance, and manifest hashes there—not in the prompt output.
- Keep prompt templates (`prompts/*.md`) in sync with the DTO fields: require JSON-only payloads, forbid extra prose or markdown fences, and document any optional fields the DTO allows.
- Micro-facts quality rule: prompts and converters must reject metadata-only statements ("entry created on…", "title is…", tag dumps). Facts and claim proposals must cite actual paragraph content via `evidence_entry` / `evidence_para` and default confidence rules.
- Before submitting a PR, run `rg 'response_model=' -n src/aijournal/commands` and confirm every match points at an approved DTO. CI has a pytest check under `tests/ci/` that enforces the same rule—keep it green.

Following this workflow keeps the project reproducible and easy to reason about for both humans and automated agents. Thank you for contributing!
