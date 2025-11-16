# aijournal Live-Mode Operator Guide (For Future Agents)

This document distills everything learned while executing the full aijournal CLI rehearsal in live mode. Follow it to reproduce the 350/350 run without relying on prior context.

--

## Coding Standards

- Always prefer simple solutions over complex ones.
- **Check for Changes**: Before starting, review the latest changes from the main branch with `git diff origin/main`
- **Commit Frequently**: Make small, frequent commits.
- **Atomic Commits**: Ensure each commit corresponds to a tested, working state.
- **Targeted Adds**: **NEVER** use `git add .`. Always add files individually (`git add <filename>`) to prevent committing unrelated changes.
- **Test Before Committing**: **NEVER** claim a task is complete without running `pytest` to ensure all tests pass.
- **Run Pre-commit Hooks**: Always run `pre-commit run --all-files` before committing to enforce code style and quality.
- **Handle Linter Issues**:
  - **False Positives**: The linter may incorrectly flag issues in `pyproject.toml`; these can be ignored.
  - **Test-Related Errors**: If a pre-commit fix breaks a test (e.g., by removing an unused but necessary fixture), suppress the warning with a `# noqa: <error_code>` comment.
- **Be Proactive**: Continuously look for opportunities to refactor and improve the codebase for better organization and readability.
- **Zero Legacy Policy**: This library has zero external users and no releases, so skip migrations/backward-compat layers and delete deprecated code instead of keeping shims.
- **Incremental Changes**: Refactor in small, testable steps. Run tests after each change and commit on success.
- **DO NOT** manually edit the CLI help messages in `README.md`. They are auto-generated.
- **NEVER** use `git add .`.
- **NEVER** claim a task is done without passing all `pytest` tests.

---

## 0. Environment Snapshot

- **Repo**: `aijournal` (main branch)
- **Python tooling**: [`uv`](https://docs.astral.sh/uv/) manages dependencies and virtualenv (`uv run …` is mandatory).
- **LLM host**: Remote Ollama at `http://192.168.1.143:11434`
  - Primary chat/advice model: `gpt-oss:20b`
  - Embedding model: `embeddinggemma` (served by the same Ollama host; no fake fallback)
- **No fake mode**: Ensure `AIJOURNAL_FAKE_OLLAMA` is **unset** whenever running live commands.
- **Testing**: `uv run pytest` (≈1.5 s). Pre-commit hooks (Ruff, Ruff-format, mypy) enforce formatting on commit.
- **Filesystem**: Live rehearsal operates in `/tmp/aijournal_live_run_YYYYMMDDhhmm`. Ground truth profile/tests remain in the repo; live artifacts stay in the temp workspace.

---

## 1. Required Reading Before Touching Code

Read these in order to understand the surfaces you will exercise. Each document targets a different audience; together they give the full picture.

1. `README.md` — product overview and quick workflow.
2. `docs/workflow.md` — day-to-day command sequence with `uv run` examples.
3. `ARCHITECTURE.md` — current system design, memory layers, retrieval, prompts, and quality targets.
4. `CONTRIBUTING.md` — development environment setup, testing, linting.
5. `docs/archive/PLAN-v0.3.md` — historical roadmap reference (skim only if you need context on past milestones).
6. `CHANGELOG.md` — review “Unreleased” for behaviour changes since the last tagged run.
7. `prompts/profile_update.md`, `prompts/interview.md`, `prompts/advise.md` — structured-output contracts.
8. `src/aijournal/commands/` & `src/aijournal/cli.py` — command runners now live under `commands/`, with `cli.py` providing thin Typer glue for `init`, `capture`, `chat`, `advise`, `status`, `serve chat`, `export pack`, and the `ops.*` namespaces (pipeline/profile/index/persona/feedback/system/dev).
9. `src/aijournal/pipelines/` — deterministic workflows backing summaries, facts, persona, profile_update, packs, and advise.
10. `src/aijournal/services/{chat.py, chat_api.py, feedback.py}` — chat orchestration, API streaming, feedback adjustments, telemetry.

Read these first to avoid surprises mid-run.

---

## 2. Standing Constraints

- **Always** run commands via `uv run …` (e.g., `uv run aijournal summarize …`) so the project virtualenv and deps stay active. Use `uv run -- bash -lc '…'` only when you need to wrap multiple shell operations.
- **Never** set `AIJOURNAL_FAKE_OLLAMA=1` during the live rehearsal; the acceptance criteria explicitly reject fake fixtures.
- **LLM server** must already host `gpt-oss:20b` and `embeddinggemma`. Verify with:
  ```bash
  uv run -- bash -lc 'export AIJOURNAL_MODEL="gpt-oss:20b" AIJOURNAL_OLLAMA_HOST="http://192.168.1.143:11434"; aijournal ollama health'
  ```
- **Set the host upfront**: before running any live-mode CLI command, export `AIJOURNAL_OLLAMA_HOST` to the remote Ollama address so chat, retrieval, and embeddings avoid defaulting to localhost.
- **Clean runs only**: if the repo has pending changes, either commit them or reset to a clean state before beginning.
- **No data loss**: Do not remove artifacts outside the temp workspace. Archive/rename instead of deleting in the repo.
- **Feedback loop**: When chat answers omit claim markers, feedback adjustments cannot apply. The chat prompt and telemetry now highlight this scenario—respond accordingly.

---

## 3. Live Rehearsal Workflow (From Scratch)

### 3.1 Seed the Workspace

1. Create a temp directory and scaffold the layout:
   ```bash
   export RUN_ROOT=/tmp/aijournal_live_run_$(date +%Y%m%d%H%M)
   uv run aijournal --path "$RUN_ROOT" init
   cd "$RUN_ROOT"
   ```

2. Capture at least five journal entries covering the last 7 days. Use `--edit` (opens `$EDITOR`) or `--text`/STDIN, making sure each entry includes rich front matter (tags, projects, mood) plus 3–4 paragraphs of body text. Example:
   ```bash
   uv run aijournal capture --edit --date 2025-10-26 --tags planning weekly-review --projects roadmap
   ```
   Repeat for the remaining days. `capture` writes canonical Markdown, snapshots the raw text, updates the manifest, normalizes the entry, and runs the downstream pipeline for the affected date.

3. To import existing folders of Markdown notes, rely on capture instead of the legacy ingest command:
   ```bash
   uv run aijournal capture --from ~/notes/weekly --source-type notes --projects roadmap
   ```
   Capture dedupes by SHA-256 and always materializes `data/journal/YYYY/MM/DD/<slug>.md`; raw snapshots land in `data/raw/`, and `data/manifest/ingested.yaml` tracks the import hash.

4. If you need to stop early (e.g., persist + normalize only), use stage filters:
   ```bash
   uv run aijournal capture --from ~/notes/weekly --max-stage 1
   ```
   Resume later with `--min-stage 2` or run the specific `aijournal ops …` command that capture prints at the end of the run.

### 3.2 LLM & Prompt Warmups

- Confirm `gpt-oss:20b` responds to structured prompts (`facts`, `profile_update`). If outputs are empty, add summaries to normalized entries or adjust wording per §4 below.

---

## 4. Prompt Calibration Lessons

Structured commands expect the model to mine existing fields (`summary`, `sections`, `tags`). Provide adequate content or the model returns empty payloads.

### Facts (`prompts/extract_facts.md`)
- LLM must emit full JSON objects (`id`, `statement`, `confidence`, `evidence`, `first_seen`, `last_seen`).
- Updated instruction instructs the model to synthesize statements from summaries/sections when paragraphs are missing.
- Validate outputs with:
  ```bash
  uv run -- bash -lc "cd $RUN_ROOT && aijournal ops pipeline extract-facts --date 2025-10-26"
  ```
  The file `derived/microfacts/<date>.yaml` should contain facts plus claim proposals. If spans are empty, that's acceptable; we log the raw text upstream.

### Profile Update (`prompts/profile_update.md`)
- Model now mines structured fields even without paragraphs. Expect claims such as “weekly planning resets align meals with training goals.”
- Validate with:
  ```bash
  uv run -- bash -lc "cd $RUN_ROOT && aijournal ops profile update --date 2025-10-26 --progress"
  ```
  Output lives at `derived/pending/profile_updates/<date>-<timestamp>.yaml`.

### Characterize
- After prompts produce meaningful payloads, run `aijournal ops profile update --date … --progress` to produce batches in `derived/pending/profile_updates/`.
- `aijournal ops pipeline review --file … --apply` now succeeds after extending `SelfProfile` with `planning`, `dashboard`, and `habits` facets.

### Chat Prompt
- It now enforces `[claim:<id>]` markers when persona claims exist. Feedback telemetry logs detected markers.
- Live commands (`chat`, `chat --feedback down/up`) should adjust claim strengths immediately.

---

## 5. Full Command Checklist (Live Mode)

Run in order, using the config env vars below unless otherwise noted.

```bash
export AIJOURNAL_MODEL="gpt-oss:20b"
export AIJOURNAL_OLLAMA_HOST="http://192.168.1.143:11434"
```

1. `uv run aijournal capture --text "Live rehearsal kickoff" --tags focus`
2. `uv run aijournal capture --from notes/weekly --source-type notes --projects roadmap`
3. `uv run aijournal capture --min-stage 2 --max-stage 5 --date YYYY-MM-DD` (rerun derivations only, if needed)
4. `uv run aijournal status`
5. `uv run aijournal chat 'What progress did I make?' --session live-verify --top 3`
6. `uv run aijournal chat 'What progress did I make?' --session live-verify --feedback down --top 3`
7. `uv run aijournal advise 'How should I prioritize habits this week?'`
8. `uv run aijournal export pack --level L1 --format yaml`
9. `uv run aijournal export pack --level L4 --date YYYY-MM-DD --history-days 1 --format json`
10. `uv run aijournal serve chat --host 127.0.0.1 --port 8055`  
    - Hit `/chat` via curl or httpx in a separate process; confirm graceful shutdown (no stack trace).
11. `uv run aijournal ops feedback apply`
12. `uv run aijournal ops system ollama health`

Advanced/manual checks (useful for troubleshooting specific stages):

13. `uv run aijournal ops pipeline normalize data/journal/YYYY/MM/DD/<entry>.md`
14. `uv run aijournal ops pipeline summarize --date YYYY-MM-DD`
15. `uv run aijournal ops pipeline extract-facts --date YYYY-MM-DD`
16. `uv run aijournal ops profile update --date YYYY-MM-DD --progress`
17. `uv run aijournal ops profile apply --date YYYY-MM-DD --yes`
18. `uv run aijournal ops pipeline review --file derived/pending/profile_updates/<batch>.yaml --apply`
20. `uv run aijournal ops index rebuild` (refreshes `derived/index/meta.json` with the strict artifact envelope)
21. `uv run aijournal ops index search 'deep work sprint focus' --top 3 --tags focus`
22. `uv run aijournal ops persona build`
23. `uv run aijournal ops persona status`
24. `uv run aijournal ops audit provenance --fix` (scan for lingering `span.text` and redact if needed)

Maintain a run log capturing score, command, summary, artifacts, troubleshooting notes (e.g., `run_log.md` in the temp directory). This ensures reproducibility and provides evidence of the 350/350 score.

---

## 6. Applying Feedback Batches

Feedback files accumulate under `derived/pending/profile_updates/feedback_*.yaml`. After reviewing them, run:
```bash
uv run -- bash -lc "cd $RUN_ROOT && aijournal ops feedback apply"
```
This command:
- Updates matched claims in `profile/claims.yaml`
- Archives processed batches to `derived/pending/profile_updates/applied_feedback/`
- Prints a summary of strength adjustments
- Exits non-zero if nothing was applied (useful for automation)

---

## 7. Chatd Lifecycle

The retriever now opens SQLite with `check_same_thread=False`, enabling clean shutdowns. To validate:
```bash
uv run -- bash -lc "cd $RUN_ROOT && aijournal serve chat --host 127.0.0.1 --port 8055"
```
In another shell:
```bash
python - <<'PY'
import httpx
resp = httpx.post("http://127.0.0.1:8055/chat", json={"session": "verify", "question": "Summarize planning focus"})
print(resp.status_code, resp.text)
PY
```
Stop the server with SIGTERM or let it exit naturally; no `sqlite3.ProgrammingError` should appear.

---

## 8. Persona / Pack Regeneration

After profile updates, refresh persona and context bundles:
```bash
uv run -- bash -lc "cd $RUN_ROOT && aijournal ops persona build"
uv run -- bash -lc "cd $RUN_ROOT && aijournal export pack --level L1 --format yaml"
uv run -- bash -lc "cd $RUN_ROOT && aijournal export pack --level L4 --date YYYY-MM-DD --history-days 1 --format json"
```
The commands update `derived/persona/persona_core.yaml` and write pack outputs under `derived/packs/`, ensuring chat/advice surfaces reflect the latest claims/facets.

---

## 9. Post-Run Clean-Up

- Move or delete applied feedback batches from `derived/pending/profile_updates/applied_feedback/` when they are no longer needed.
- Optionally archive the entire temp workspace for audit (`tar -czf aijournal_live_run_YYYYMMDDhhmm.tar.gz $RUN_ROOT`).
- Ensure the main repo tree is still clean (`git status -sb`).

---

## 10. Quick Checklist (TL;DR)

1. Read required docs (README, workflow, architecture, prompts, key services).
2. `aijournal init` into `/tmp/aijournal_live_run_*`; capture at least five detailed entries with `aijournal capture --edit/--text` (or `--from`).
3. Let capture drive normalization and derivations automatically; rerun specific stages with `--min/--max-stage` or `aijournal ops pipeline …` when inspecting issues.
4. Verify downstream artifacts as needed (summaries, micro-facts, profile updates/review) via the `ops` commands.
5. Regenerate index/persona when troubleshooting (`aijournal ops index rebuild`, `aijournal ops persona build`) and confirm searches succeed.
6. Export packs with `aijournal export pack --level …` once persona is fresh.
7. Exercise chat (`chat`, `chat --feedback`, `serve chat` + POST), confirm claim markers, apply feedback (`ops feedback apply`).
8. Run `aijournal ops system ollama health` for provenance.
9. Record everything in a run log; aim for 350/350.
10. Run `uv run pytest` before committing any code changes.

Following these steps ensures a clean, reproducible live-mode rehearsal aligned with the latest plan objectives. Good luck, and keep the tree green! 
