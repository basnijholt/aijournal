# Ruff Audit – November 15, 2025

## Command History

- `ruff check .` (dry run)
- `ruff check --fix .`
- `ruff check --statistics .`

`ruff check --fix .` still exits 1 with 270 violations because most rules require manual edits. The sections below group the remaining issues so we can plan incremental fixes instead of attempting an all-at-once cleanup.

## Issue Categories

| Category | Rules (count) | Representative files | Notes / Next Steps |
| --- | --- | --- | --- |
| **Implicit namespace packages** | `INP001` (0 → resolved) | `scripts/*.py`, `tests/**/test_*.py` | Added descriptive `__init__.py` sentinels to `scripts/` and every flagged `tests/` subtree so import tools treat them as concrete packages. |
| **CLI option/argument wiring** | `FBT003` (26) | `src/aijournal/cli.py` | Moved every `typer.Option`/`Argument` default involved in B008 into module-level singletons, so only boolean positional-value cleanups remain. Next up: convert `--flag/--no-flag` patterns to keyword args or `Annotated` helpers to quiet `FBT003`. |
| **Cyclomatic complexity & branching** | `C901` (29), `PLR0912` (10), `PLR0915` (12) | `src/aijournal/cli.py`, `src/aijournal/simulator/validators.py`, `tests/services/test_capture.py` | Break long functions into helpers (e.g., split `capture` command into parse + execute steps). For test helpers, wrap repeated monkeypatch/setup logic in fixtures. Prioritize runtime code first, then revisit tests. |
| **Exception hygiene** | `BLE001` (28), `S112` (2), `B904` (4), `TRY301` (1), `TRY004` (1) | `scripts/check_schemas.py`, `src/aijournal/simulator/validators.py`, `tests/test_retriever.py` | Replace blanket `except Exception`/`BaseException` with narrower exception types or document with logging. When re-raising inside `except`, use `raise ... from err`. |
| **Magic numbers & performance** | `PLR2004` (20), `PERF401` (15) | `src/aijournal/services/persona_export.py`, `src/aijournal/simulator/validators.py`, `scripts/repro_devstral_schema_error.py` | Hoist thresholds (e.g., persona budgets, stage limits) into named constants. Replace append-in-loop patterns with comprehensions or `list.extend` where the data flow is purely transformational. |
| **Docs & annotations** | `D100/D104/D105/D417` (15 combined), `ANN201/ANN202` (5), `ARG001/ARG002` (9) | `scripts/check_structured_metrics.py`, `src/aijournal/cli.py`, `src/aijournal/simulator/orchestrator.py` | Add short docstrings for public modules/magic methods, describe Typer callback parameters, and annotate private helpers (especially context managers) with explicit return types. Remove unused parameters or rename them to `_unused`. |
| **Tests & temp-path safety** | `S108` (3), `B017` (1), `PT011` (2), `PT018` (1), `DTZ001` (1), `RUF012` (4) | `tests/commands/test_microfact_prompts.py`, `tests/common/test_meta.py`, `tests/services/test_tolerant.py`, `tests/test_workspace.py` | Replace `/tmp` literals with `tmp_path` fixtures, tighten `pytest.raises` expectations, split composite assertions, provide `tzinfo` when constructing datetimes, and annotate mutable class attributes with `ClassVar`. |
| **Executable scripts** | `EXE001` (2) | `scripts/check_structured_metrics.py`, `scripts/repro_devstral_schema_error.py` | Either drop the shebangs or `chmod +x` the files (preferred so they remain runnable as scripts). |

## Suggested Remediation Order

1. **Add package sentinels** (`__init__.py`) for `scripts/` and every `tests/` subtree; this resolves 32 errors with minimal risk.
2. **Normalize Typer command wiring** by extracting reusable option/argument factories, then refactor high-complexity CLI commands while they are already being edited.
3. **Handle exception/magic-number/perf rules** in simulator + persona code (domain logic), deferring test-only fixes until runtime code is stable.
4. **Tighten tests** to satisfy security/style rules once the production modules are clean.

Track progress by re-running `ruff check --statistics .` after each milestone—the counts should drop per category if the plan above is followed.
