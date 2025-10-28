# aijournal Workflow Guide (New User Overview)

This guide explains how the main commands fit together, the order in which to run them, and the minimum data you need. Start here after reading the introduction in `README.md`.

---

## 1. Prerequisites

- You’ve cloned the repository and installed [`uv`](https://docs.astral.sh/uv/).
- You can run `uv run pytest` successfully (this confirms the virtual environment is set up).
- If you plan to run in live mode, ensure an Ollama server is available (see `README` for model choices). For local experiments you can keep using the fake LLM mode (`AIJOURNAL_FAKE_OLLAMA=1`).

---

## 2. First-Time Setup

1. **Initialize a workspace**  
   ```bash
   uv run aijournal init --path /path/to/my_journal
   ```  
   This creates the directory layout (`data/`, `profile/`, `derived/`, etc.).

2. **Author entries**  
   Add Markdown journal files under `data/journal/YYYY/MM/DD/slug.md`. Each entry should have front matter (`id`, `created_at`, `title`, `tags`, `projects`, `mood`) and a body with a few paragraphs.

3. **Normalize entries**  
   ```bash
   uv run -- bash -lc 'cd /path/to/my_journal && aijournal normalize data/journal/2025/10/26/slug.md'
   ```  
   Normalized YAML lives in `data/normalized/YYYY-MM-DD/`. Check that `summary` fields exist; add one manually if the auto-normalizer doesn’t infer it.

---

## 3. Daily Pipeline Overview

Once you have normalized entries for a given day, run the following commands **in order**. Each step produces files that the next step consumes.

| Step | Command | Purpose | Output |
|------|---------|---------|--------|
| 1 | `aijournal summarize --date YYYY-MM-DD` | Creates a daily narrative | `derived/summaries/YYYY-MM-DD.yaml` |
| 2 | `aijournal facts --date YYYY-MM-DD` | Generates micro-facts and claim proposals | `derived/microfacts/YYYY-MM-DD.yaml` |
| 3 | `aijournal profile suggest --date YYYY-MM-DD` | Suggests new claims/facets | `derived/profile_suggestions/YYYY-MM-DD.yaml` |
| 4 | `aijournal profile apply --date YYYY-MM-DD --yes` | Applies suggestions (if any) | Updates `profile/claims.yaml` / `self_profile.yaml` |
| 5 | `aijournal characterize --date YYYY-MM-DD --progress` | Consolidates updates & interview prompts | `derived/pending/profile_updates/*.yaml` |
| 6 | `aijournal review-updates --file <batch> --apply` | Merges pending updates into the profile | Updates `profile/` files |

> Tip: repeat steps 5–6 for each batch the characterize step produces.

---

## 4. Retrieval & Persona Maintenance

After the daily pipeline, refresh the artifacts used by chat, search, and advice:

1. **Rebuild the search index**  
   ```bash
   uv run -- bash -lc 'cd /path/to/my_journal && aijournal index rebuild'
   ```  
   Artifacts appear in `derived/index/`.

2. **Run a smoke search** (optional but verifies the index)  
   ```bash
   uv run -- bash -lc 'cd /path/to/my_journal && aijournal index search "deep work sprint focus" --top 3'
   ```

3. **Regenerate the persona core**  
   ```bash
   uv run -- bash -lc 'cd /path/to/my_journal && aijournal persona build'
   ```

4. **Pack the context bundle**  
   ```bash
   uv run -- bash -lc 'cd /path/to/my_journal && aijournal pack --level L1 --format yaml'
   ```
   Use `--level L4` when you need a larger bundle for external assistants.

---

## 5. Conversational Surfaces

With the profile, index, and packs up to date you can use the interactive commands:

- **Chat (CLI)**  
  ```bash
  uv run -- bash -lc 'cd /path/to/my_journal && aijournal chat "What progress did I make yesterday?" --session daily-review --top 3'
  ```  
  Add `--feedback up|down` to nudge claim strengths. Chat automatically saves transcripts when `--save` is enabled (default).

- **Chat daemon (API)**  
  ```bash
  uv run -- bash -lc 'cd /path/to/my_journal && aijournal chatd --host 127.0.0.1 --port 8055'
  ```  
  Use `curl` or `httpx` to POST to `/chat`.

- **Advisor**  
  ```bash
  uv run -- bash -lc 'cd /path/to/my_journal && aijournal advise "How should I prioritise habits this week?"'
  ```

- **Feedback batches**  
  When you review chat feedback later, apply it in bulk:
  ```bash
  uv run -- bash -lc 'cd /path/to/my_journal && aijournal feedback-apply'
  ```

---

## 6. Optional / Advanced Commands

- `aijournal ingest <path>` — normalize external Markdown (blog posts, etc.).
- `aijournal profile status` — shows review priorities after applying updates.
- `aijournal interview --date YYYY-MM-DD` — generates follow-up questions for that day’s entries.
- `aijournal pack --level L4 --date YYYY-MM-DD --history-days N --format json` — build a long-horizon pack for external assistants.
- `aijournal ollama health` — verifies available models on the Ollama host.

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

Keep this workflow handy whenever you add new entries or revisit older notes. Once you’re comfortable with the ordering, you can automate sections (e.g., a daily script) or integrate the commands into your own tooling. Happy journaling!
