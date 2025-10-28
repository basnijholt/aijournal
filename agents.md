# aijournal Live-Mode Operator Guide (For Future Agents)

This document distills everything learned while executing the full aijournal CLI rehearsal in live mode. Follow it to reproduce the 350/350 run without relying on prior context.

---

## 0. Environment Snapshot

- **Repo**: `aijournal` (main branch)
- **Python tooling**: [`uv`](https://docs.astral.sh/uv/) manages dependencies and virtualenv (`uv run …` is mandatory).
- **LLM host**: Remote Ollama at `http://192.168.1.143:11434`
  - Primary chat/advice model: `gpt-oss:20b`
  - Embedding model: `nomic-embed-text` (served by the same Ollama host; no fake fallback)
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
7. `prompts/characterize.md`, `prompts/interview.md`, `prompts/advise.md` — structured-output contracts.
8. `src/aijournal/cli.py` — Typer entry points: `init`, `normalize`, `summarize`, `facts`, `profile ...`, `persona ...`, `index ...`, `pack`, `chat`, `chatd`, `advise`, `feedback-apply`.
9. `src/aijournal/services/{chat.py, chat_api.py, feedback.py}` — chat orchestration, API streaming, feedback adjustments, telemetry.

Read these first to avoid surprises mid-run.

---

## 2. Standing Constraints

- **Always** run commands via `uv run …` (e.g., `uv run aijournal summarize …`) so the project virtualenv and deps stay active. Use `uv run -- bash -lc '…'` only when you need to wrap multiple shell operations.
- **Never** set `AIJOURNAL_FAKE_OLLAMA=1` during the live rehearsal; the acceptance criteria explicitly reject fake fixtures.
- **LLM server** must already host `gpt-oss:20b` and `nomic-embed-text`. Verify with:
  ```bash
  uv run -- bash -lc 'export AIJOURNAL_MODEL="gpt-oss:20b" AIJOURNAL_OLLAMA_HOST="http://192.168.1.143:11434"; aijournal ollama health'
  ```
- **Clean runs only**: if the repo has pending changes, either commit them or reset to a clean state before beginning.
- **No data loss**: Do not remove artifacts outside the temp workspace. Archive/rename instead of deleting in the repo.
- **Feedback loop**: When chat answers omit claim markers, feedback adjustments cannot apply. The chat prompt and telemetry now highlight this scenario—respond accordingly.

---

## 3. Live Rehearsal Workflow (From Scratch)

### 3.1 Seed the Workspace

1. Create temp directory:
   ```bash
   export RUN_ROOT=/tmp/aijournal_live_run_$(date +%Y%m%d%H%M)
   uv run aijournal init --path "$RUN_ROOT"
   ```
   Directory structure: `config/`, `data/`, `profile/`, `derived/`, etc.

2. Change into the run directory:
   ```bash
   cd "$RUN_ROOT"
   ```

3. Create at least five Markdown journal entries covering the last 7 days. Each entry needs front matter (`id`, `created_at`, `title`, `tags`, `projects`, `mood`) plus 3–4 paragraphs of body text. Write them manually—no fake flags.

4. Normalize every journal:
   ```bash
   uv run aijournal normalize data/journal/YYYY/MM/DD/entry.md
   ```
   - Ensure `data/normalized/<date>/<slug>.yaml` contains `summary` fields; add manually if normalization is sparse.

5. Optional ingestion (if external Markdown exists):
   ```bash
   uv run aijournal ingest /path/to/external.md
   ```

6. Maintain a manifest table (date, slug, tags) to reuse the correct dates later.

### 3.2 LLM & Prompt Warmups

- Confirm `gpt-oss:20b` responds to structured prompts (`facts`, `profile_suggest`, `characterize`). If outputs are empty, add summaries to normalized entries or adjust wording per §4 below.

---

## 4. Prompt Calibration Lessons

Structured commands expect the model to mine existing fields (`summary`, `sections`, `tags`). Provide adequate content or the model returns empty payloads.

### Facts (`prompts/extract_facts.md`)
- LLM must emit full JSON objects (`id`, `statement`, `confidence`, `evidence`, `first_seen`, `last_seen`).
- Updated instruction instructs the model to synthesize statements from summaries/sections when paragraphs are missing.
- Validate outputs with:
  ```bash
  uv run -- bash -lc "cd $RUN_ROOT && aijournal facts --date 2025-10-26 --timeout 180"
  ```
  The file `derived/microfacts/<date>.yaml` should contain facts plus claim proposals. If spans are empty, that's acceptable; we log the raw text upstream.

### Profile Suggestions (`prompts/profile_suggest.md`)
- Model now mines structured fields even without paragraphs. Expect claims such as “weekly planning resets align meals with training goals.”
- Validate with:
  ```bash
  uv run -- bash -lc "cd $RUN_ROOT && aijournal profile suggest --date 2025-10-26 --timeout 180"
  ```
  Output lives at `derived/profile_suggestions/<date>.yaml`.

### Characterize
- After prompts produce meaningful payloads, run `aijournal characterize --date … --progress` to produce batches in `derived/pending/profile_updates/`.
- `aijournal review-updates --file … --apply` now succeeds after extending `SelfProfile` with `planning`, `dashboard`, and `habits` facets.

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

1. `uv run aijournal summarize --date YYYY-MM-DD`
2. `uv run aijournal facts --date YYYY-MM-DD --timeout 180`
3. `uv run aijournal profile suggest --date YYYY-MM-DD --timeout 180`
4. `uv run aijournal profile apply --date YYYY-MM-DD --yes`
5. `uv run aijournal profile status`
6. `uv run aijournal characterize --date YYYY-MM-DD --progress --timeout 240`
7. `uv run aijournal review-updates --file derived/pending/profile_updates/<batch>.yaml --apply`
8. (Repeat characterize/review for each new entry date)
9. `uv run aijournal index rebuild`
10. `uv run aijournal index search 'deep work sprint focus' --top 3 --tags focus`  
    (example query that yields a match)
11. `uv run aijournal persona build`
12. `uv run aijournal persona status`
13. `uv run aijournal interview --date YYYY-MM-DD`
14. `uv run aijournal advise 'How should I prioritize habits this week?'`
15. `uv run aijournal chat 'What progress did I make?' --session live-verify --top 3 --no-save`
16. `uv run aijournal chat 'What progress did I make?' --session live-verify --feedback down --top 3 --no-save`
17. `uv run aijournal chatd --host 127.0.0.1 --port 8055`  
    - Hit `/chat` via curl or httpx in a separate process; confirm graceful shutdown (no stack trace).
18. `uv run aijournal pack --level L1 --format yaml`
19. `uv run aijournal pack --level L4 --date YYYY-MM-DD --history-days 1 --format json`
20. `uv run aijournal feedback-apply`  
    (applies pending feedback batches and archives them)
21. `uv run aijournal ollama health`

Maintain a run log capturing score, command, summary, artifacts, troubleshooting notes (e.g., `run_log.md` in the temp directory). This ensures reproducibility and provides evidence of the 350/350 score.

---

## 6. Applying Feedback Batches

Feedback files accumulate under `derived/pending/profile_updates/feedback_*.yaml`. After reviewing them, run:
```bash
uv run -- bash -lc "cd $RUN_ROOT && aijournal feedback-apply"
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
uv run -- bash -lc "cd $RUN_ROOT && /Users/bas.nijholt/Downloads/aijournal/.venv/bin/aijournal chatd --host 127.0.0.1 --port 8055"
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
uv run -- bash -lc "cd $RUN_ROOT && aijournal persona build"
uv run -- bash -lc "cd $RUN_ROOT && aijournal pack --level L1 --format yaml"
uv run -- bash -lc "cd $RUN_ROOT && aijournal pack --level L4 --date YYYY-MM-DD --history-days 1 --format json"
```
These commands guarantee the chat/advice surfaces reflect the latest claims/facets.

---

## 9. Post-Run Clean-Up

- Move or delete applied feedback batches from `derived/pending/profile_updates/applied_feedback/` when they are no longer needed.
- Optionally archive the entire temp workspace for audit (`tar -czf aijournal_live_run_YYYYMMDDhhmm.tar.gz $RUN_ROOT`).
- Ensure the main repo tree is still clean (`git status -sb`).

---

## 10. Quick Checklist (TL;DR)

1. Read required docs (README, workflow, architecture, prompts, key services).
2. `aijournal init` into `/tmp/aijournal_live_run_*`; generate at least five detailed Markdown entries (with summaries).
3. Normalize every entry (ensure summaries exist).
4. Run structured commands (summarize, facts, profile suggest/apply, characterize, review-updates).
5. Regenerate index (`index rebuild`) and verify at least one successful search.
6. Rebuild persona and packs (`persona build`, `pack --level …`).
7. Exercise chat (`chat`, `chat --feedback`, `chatd` + POST), confirm claim markers, apply feedback (`feedback-apply`).
8. Run `ollama health` for provenance.
9. Record everything in a run log; aim for 350/350.
10. Run `uv run pytest` before committing any code changes.

Following these steps ensures a clean, reproducible live-mode rehearsal aligned with the latest plan objectives. Good luck, and keep the tree green! 
