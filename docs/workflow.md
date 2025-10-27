# aijournal Workflow Guide (New User Overview)

This guide explains how the main commands fit together, the order in which to run them, and the minimum data you need. Start here after reading the introduction in `README.md`.

---

## 1. Prerequisites

- You’ve cloned the repository and installed [`uv`](https://docs.astral.sh/uv/).
- You can run `uv run pytest` successfully (this confirms the virtual environment is set up).
- If you plan to run in live mode, ensure an Ollama server is available (see `README` for model choices). For local experiments you can keep using the fake LLM mode (`AIJOURNAL_FAKE_OLLAMA=1`).
- Before starting the daily pipeline in live mode, export `AIJOURNAL_OLLAMA_HOST` to the remote Ollama address so CLI calls don’t fall back to localhost.

---

## 2. First-Time Setup

1. **Initialize a workspace**  
   ```bash
   uv run aijournal init --path /path/to/my_journal
   ```  
   This creates the directory layout (`data/`, `profile/`, `derived/`, etc.).

2. **Enter the workspace**  
   ```bash
   cd /path/to/my_journal
   ```  
   All subsequent commands assume you run them from this directory with `uv run aijournal ...`.

3. **Author entries**  
   Add Markdown journal files under `data/journal/YYYY/MM/DD/slug.md`. Each entry should have front matter (`id`, `created_at`, `title`, `tags`, `projects`, `mood`) and a body with a few paragraphs.

4. **Normalize entries**  
   ```bash
   uv run aijournal normalize data/journal/2025/10/26/slug.md
   ```  
   Normalized YAML lives in `data/normalized/YYYY-MM-DD/`. Check that `summary` fields exist; add one manually if the auto-normalizer doesn’t infer it.

---

## 3. Daily Pipeline Overview

Once you have normalized entries for a given day, run the following commands **in order**. Each step produces files that the next step consumes.

| Step | Command | Purpose | Output |
|------|---------|---------|--------|
| 1 | `uv run aijournal summarize --date YYYY-MM-DD` | Creates a daily narrative | `derived/summaries/YYYY-MM-DD.yaml` |
| 2 | `uv run aijournal facts --date YYYY-MM-DD` | Generates micro-facts and claim proposals | `derived/microfacts/YYYY-MM-DD.yaml` |
| 3 | `uv run aijournal profile suggest --date YYYY-MM-DD` | Suggests new claims/facets | `derived/profile_suggestions/YYYY-MM-DD.yaml` |
| 4 | `uv run aijournal profile apply --date YYYY-MM-DD --yes` | Applies suggestions (if any) | Updates `profile/claims.yaml` / `self_profile.yaml` |
| 5 | `uv run aijournal characterize --date YYYY-MM-DD --progress` | Consolidates updates & interview prompts | `derived/pending/profile_updates/*.yaml` |
| 6 | `uv run aijournal review-updates --file <batch> --apply` | Merges pending updates into the profile | Updates `profile/` files |

> Tip: repeat steps 5–6 for each batch the characterize step produces.

---

## 4. Retrieval & Persona Maintenance

After the daily pipeline, refresh the artifacts used by chat, search, and advice:

1. **Rebuild the search index**  
   ```bash
   uv run aijournal index rebuild
   ```  
   Artifacts appear in `derived/index/`.

2. **Run a smoke search** (optional but verifies the index)  
   ```bash
   uv run aijournal index search "deep work sprint focus" --top 3
   ```

3. **Regenerate the persona core**  
   ```bash
   uv run aijournal persona build
   ```

4. **Pack the context bundle**  
   ```bash
   uv run aijournal pack --level L1 --format yaml
   ```
   Use `--level L4` when you need a larger bundle for external assistants.

---

## 5. Conversational Surfaces

With the profile, index, and packs up to date you can use the interactive commands:

- **Chat (CLI)**  
  ```bash
  uv run aijournal chat "What progress did I make yesterday?" --session daily-review --top 3
  ```  
  Add `--feedback up|down` to nudge claim strengths. Chat automatically saves transcripts when `--save` is enabled (default).

- **Chat daemon (API)**  
  ```bash
  uv run aijournal chatd --host 127.0.0.1 --port 8055
  ```  
  Use `curl` or `httpx` to POST to `/chat`.

- **Advisor**  
  ```bash
  uv run aijournal advise "How should I prioritise habits this week?"
  ```

- **Feedback batches**  
  When you review chat feedback later, apply it in bulk:
  ```bash
  uv run aijournal feedback-apply
   ```

---

## 6. Optional / Advanced Commands

- `uv run aijournal ingest <path>` — normalize external Markdown (blog posts, etc.).
- `uv run aijournal profile status` — shows review priorities after applying updates.
- `uv run aijournal interview --date YYYY-MM-DD` — generates follow-up questions for that day’s entries.
- `uv run aijournal pack --level L4 --date YYYY-MM-DD --history-days N --format json` — build a long-horizon pack for external assistants.
- `uv run aijournal ollama health` — verifies available models on the Ollama host.

---

## 7. Quick Reference Flow

```
init → write journal entries → normalize
   ↓
summarize → facts → profile suggest → profile apply
   ↓
characterize → review-updates
   ↓
index rebuild → persona build → pack
   ↓
chat / advise / interview
   ↓
feedback-apply (as needed)
```

Running commands in this order ensures downstream surfaces (chat, packs, advice) always see up-to-date data without manual patching.

---

## 8. Developer Notes

The runtime is now split between small, testable modules:

- `src/aijournal/commands/` handles orchestration for each Typer command—file system inputs/outputs, retries, and user messaging live here.
- `src/aijournal/pipelines/` contains deterministic workflows that combine services and prompts (summaries, facts, persona, packs, characterize, advise). Pipelines never touch Typer directly, making them easy to unit test.
- `src/aijournal/services/` keeps reusable integrations (Ollama client, retriever, chat API, feedback).

If you need to extend a command, start with the relevant `commands/*.py` module and only dip into pipelines/services when you need new orchestration steps. Keep CLI changes limited to wiring so the high-level flow in this guide stays stable.

---

Keep this workflow handy whenever you add new entries or revisit older notes. Once you’re comfortable with the ordering, you can automate sections (e.g., a daily script) or integrate the commands into your own tooling. Happy journaling!
