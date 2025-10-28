# aijournal Refactor Playbook (Coding Agent Entry Point)

## Before You Start

- **Read & absorb the project charter.** Begin with `PLAN.md` (full roadmap, guardrails) and `README.md` (usage + runtime modes).
- **Review key modules.**
  - `src/aijournal/cli.py`: the monolith coordinating all commands.
  - `src/aijournal/models/__init__.py`: the large Pydantic registry (authoritative + derived + LLM response schemas).
  - `tests/test_cli_*.py`: high-level CLI regression tests exercising fake Ollama mode.
- **Understand runtime conventions.**
  - Use fake LLM mode (`AIJOURNAL_FAKE_OLLAMA=1`) for deterministic runs.
  - Tests run via `uv run pytest` (≈2 seconds for the full suite).
- **Git etiquette.**
  - Never rewrite history (per PLAN). Small, reviewable commits only.
  - Respect existing formatting/linting (Ruff, mypy) before committing.

## Overall Objective

Decompose the 5.8k LOC `cli.py` and 700 LOC `models/__init__.py` into modular packages (`utils`, `pipelines`, `commands`). Preserve behavior via strengthened tests so we can iterate without a monolith.

## Safety & Commit Discipline

- Treat each checklist item as a **series of micro-steps**. When in doubt, split further (e.g., move one helper at a time, then swap the caller in the next commit).
- After every micro-step, run `uv run pytest`. Only proceed when the suite is green.
- Keep diffs focused: no drive-by edits, no unrelated cleanup. The code must remain functional after every commit.
- Leave explanatory TODO comments out of the code—capture follow-ups in issues or in this document instead.
- If a refactor uncovers a behavior bug, note it inline (e.g., in `@testing-coverage-plan.md`) and continue structuring the code without “fixing” logic mid-step unless the plan calls for it.

---

## Refactor Checklist

> **Rule:** After every numbered task, run the full suite (`uv run pytest`). Commit only when the suite is green.
> **Note:** Make a dedicated commit immediately after completing each individual step.

---

## Phase 0 · Harden CLI Tests

1. [x] Add `tests/conftest.py` fixture `cli_workspace(tmp_path, monkeypatch)`:
   - `monkeypatch.chdir(tmp_path)` so commands run inside the temporary workspace
   - set `AIJOURNAL_FAKE_OLLAMA=1`
   - run `aijournal init` to seed the directory structure
   - monkeypatch time helpers (e.g., `aijournal.utils.time.now`) to a fixed datetime
   - `yield tmp_path` for callers  
   → `uv run pytest`

2. [x] Update `tests/test_cli_summarize.py` to use `cli_workspace`, drop duplicated setup, and assert key fields instead of full snapshots.  
   → `uv run pytest`

3. [x] Update `tests/test_cli_facts.py` to use `cli_workspace`; assert claim proposals and preview events deterministically.  
   → `uv run pytest`

4. [x] Update `tests/test_cli_advise.py` to use `cli_workspace`; assert assumptions/steps instead of filename checks.  
   → `uv run pytest`

5. [x] Repeat deterministic setup/assertions for remaining CLI suites that spin workspaces (`test_cli_pack`, `test_cli_persona`, `test_cli_profile_*`, etc.).  
   → `uv run pytest`

---

## Phase 1 · Utilities Foundation (No Behavior Change)

6. [x] Expand `src/aijournal/utils/__init__.py` with a short docstring explaining it houses stateless, project-wide helpers.  
   → `uv run pytest`

7. [x] Move path constants/mappers from `cli.py` into `utils/paths.py`; update imports.  
   → `uv run pytest`

8. [x] Move time/format helpers (`now()`, `format_timestamp`, slug helpers, session id) into `utils/time.py`; update code/tests.  
   → `uv run pytest`

9. [x] Adjust fixtures/tests to patch the new location (`aijournal.utils.time`).  
   → `uv run pytest`

---

## Phase 2 · Models Decomposition

10. [x] Create `models/authoritative.py` (ManifestEntry, JournalEntry, NormalizedEntry, ClaimsFile, SelfProfile, etc.) and re-export in `models/__init__.py` (keep `claim_atoms.py`/`base.py` imports consistent).  
    → `uv run pytest`

11. [x] Create `models/derived.py` (DailySummary, MicroFactsFile, AdviceCard, PersonaCore*, IndexMeta, …) and re-export.  
    → `uv run pytest`

12. [x] Create `models/responses.py` (DailySummaryResponse, ExtractedFactsResponse, CharacterizeResponse, SimpleProfileSuggestionsResponse, …); update imports.  
    → `uv run pytest`

13. [x] Update `aijournal/schema.py` registry to use new modules.  
    → `uv run pytest`

---

## Phase 3 · Domain Support Modules

14. [x] Create `src/aijournal/fakes.py` (or `fakes/__init__.py`) and move `_fake_*` generators from `cli.py`; update CLI/tests.  
    → `uv run pytest`

15. [x] Confirm fake outputs remain deterministic; tweak tests if needed.  
    → `uv run pytest`

---

## Phase 4 · Pipeline Modules

_For each pipeline: (1) add the new module + unit test, commit; (2) update the CLI to call it, commit (re-run `uv run pytest` after each). Include module docstrings clarifying that pipelines orchestrate services and I/O for a single use case._

16. [x] Create `pipelines/__init__.py` with a docstring distinguishing pipelines from services (pipelines orchestrate; services provide reusable capabilities).  
    → `uv run pytest`

17. [x] Add `pipelines/normalization.py`; move `_normalize_*`, `_simple_claim_to_upsert`, `_clean_summary`, etc., from `cli.py`; add unit tests (e.g., `_normalize_claim_atom`).  
    → `uv run pytest`

18. [x] Add `pipelines/summarize.py` (`generate_summary`), refactor CLI to call it, add `tests/pipelines/test_summarize.py`.  
    → `uv run pytest`

19. [x] Add `pipelines/facts.py` (`generate_microfacts`), update CLI/tests.  
    → `uv run pytest`

20. [x] Add `pipelines/persona.py`, update CLI/tests.  
    → `uv run pytest`

21. [x] Add `pipelines/characterize.py`, update CLI/tests.  
    → `uv run pytest`

22. [x] Add `pipelines/advise.py`, update CLI/tests.  
    → `uv run pytest`

23. [x] Add `pipelines/index.py`, update CLI/tests.  
    → `uv run pytest`

24. [x] Add `pipelines/pack.py`, update CLI/tests.  
    → `uv run pytest`

---

## Phase 5 · Commands Package

_Pattern for each command group: first create `commands/<name>.py` with a thin `run_*` wrapper replicated from `cli.py`, commit; then switch the Typer command to call it, commit._

25. [x] Create `commands/__init__.py` (docstring: commands host CLI orchestration logic).  
    → `uv run pytest`

26. [x] Add `commands/init.py` with `run_init(...)`; Typer wrapper calls it.  
    → `uv run pytest`

27. [x] Add `commands/new.py` with `run_new(...)`; update tests.  
    → `uv run pytest`

28. [x] Add `commands/ingest.py`; move ingest orchestration and ensure dependent tests use the new path.  
    → `uv run pytest`

29. [x] Add `commands/summarize.py` to orchestrate IO + pipeline; thin CLI wrapper.  
    → `uv run pytest`

30. [x] Add `commands/facts.py`.  
    → `uv run pytest`

31. [x] Add `commands/persona.py`.  
    → `uv run pytest`

32. [x] Add `commands/profile.py` (suggest/apply/status).  
    → `uv run pytest`

33. [x] Add `commands/index.py`.  
    → `uv run pytest`

34. [x] Add `commands/pack.py`.  
    → `uv run pytest`

35. [x] Add `commands/chat.py`/`chatd.py` wrappers.  
    → `uv run pytest`

36. [x] Add `commands/advise.py`.  
    → `uv run pytest`

37. [x] Add `commands/characterize.py`.  
    → `uv run pytest`

---

## Phase 6 · Final Polish

38. [x] Run `ruff --select F401,F841 --fix` (commit resulting cleanup).  
    → `uv run pytest`

39. [x] Review `cli.py` to ensure it’s mostly Typer glue + docstrings; adjust comments.  
    → `uv run pytest`

40. [x] Update docs (README/PLAN) to describe the new module layout (include distinction between services vs. pipe[ ] lines).  
    → `uv run pytest`

41. [x] Confirm CLI smoke/integration tests remain in place to guard end-to-end behaviour (full `tests/test_cli_*` suite still green).  
    → `uv run pytest`

---

👏 Follow the steps sequentially, keep commits small, and ensure `uv run pytest` stays green after every change.
