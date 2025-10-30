# aijournal Workflow Guide (New User Overview)

This guide explains how the main commands fit together, the order in which to run them, and the minimum data you need. Start here after reading the introduction in `README.md`.

---

## 1. Prerequisites

- You’ve cloned the repository and installed [`uv`](https://docs.astral.sh/uv/).
- You can run `uv run pytest` successfully (this confirms the virtual environment is set up).
- If you plan to run in live mode, ensure an Ollama server is available (see `README` for model choices). For local experiments you can keep using the fake LLM mode (`AIJOURNAL_FAKE_OLLAMA=1`).
- Record your shared Ollama endpoint in `config/config.yaml` (`host: http://192.168.1.143:11434`). Command-line overrides still work the same way: per-command flags win, then environment variables (`AIJOURNAL_OLLAMA_HOST` / `OLLAMA_BASE_URL`), then the config file, and finally the local default.

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

3. **Capture your first entry**  
   ```bash
   uv run aijournal capture --text "Kickoff journal entry" --tag planning --project onboarding
   ```  
   `capture` writes the canonical Markdown file, records a manifest row and raw snapshot, and runs the full downstream pipeline (normalize → summarize → extract-facts → profile suggest/apply → characterize/review) for any changed dates. Re-run `capture` whenever you add entries or import folders—it automatically dedupes by hash and skips work when nothing changed.

---

## 3. Everyday Flow

The top-level CLI now covers the common lifetime loop:

1. **Capture new material**  
 ```bash
  uv run aijournal capture --text "Highlights from the product sync" --tag focus
  uv run aijournal capture --from notes/weekly --source-type notes --project roadmap
  ```  
  `capture` handles canonical Markdown writes, manifest updates, raw snapshots, and runs the downstream pipeline (normalize → summarize → extract-facts → profile suggest/apply → characterize/review). It only touches dates whose content actually changed.

   Need to stop early or drive the pipeline manually? Use stage filters:
   ```bash
   uv run aijournal capture --from notes/weekly --max-stage 1      # persist + normalize only
   uv run aijournal capture --from notes/weekly --min-stage 2      # rerun derived stages
   ```
   Capture always revalidates stages 0–1 (persist/normalize) so the canonical files stay in sync, then executes any requested stages ≥2. The CLI prints remaining stages along with the equivalent `uv run aijournal ops ...` commands if you want to pick up manually.

2. **Check workspace status**  
   ```bash
   uv run aijournal status
   ```  
   Confirms persona/index freshness, pending profile batches, and Ollama connectivity. Any warnings show up in yellow.

3. **Use conversational surfaces**  
   - `uv run aijournal chat "What progress did I make?"`  
   - `uv run aijournal advise "How should I prioritise habits this week?"`  
   - `uv run aijournal export pack --level L1 --format yaml`  
   - `uv run aijournal serve chat --host 127.0.0.1 --port 8055`

4. **Apply feedback when ready**  
   ```bash
   uv run aijournal ops feedback apply
   ```

That’s the entire daily workflow—no manual normalization or staged pipeline runs required.

---

## 4. Retrieval & Persona Maintenance

`capture` already refreshes the index, persona core, and packs when inputs change. You can re-run
individual stages manually via the `ops` namespace when debugging or scripting:

- `uv run aijournal ops index rebuild` — rebuild Annoy/SQLite artifacts from scratch.  
- `uv run aijournal ops index search "deep work" --top 3` — smoke-test the index.  
- `uv run aijournal ops persona build` — regenerate `derived/persona/persona_core.yaml`.  
- `uv run aijournal export pack --level L4 --history-days 1` — assemble a context bundle (top-level everyday command).

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
  uv run aijournal serve chat --host 127.0.0.1 --port 8055
  ```  
  Use `curl` or `httpx` to POST to `/chat`.

- **Advisor**  
  ```bash
  uv run aijournal advise "How should I prioritise habits this week?"
  ```

- **Feedback batches**  
  When you review chat feedback later, apply it in bulk:
  ```bash
  uv run aijournal ops feedback apply
   ```

---

## 6. Optional / Advanced Commands

- `uv run aijournal ops pipeline ingest <path>` — run the legacy ingestion agent without invoking capture.
- `uv run aijournal ops profile status` — detailed review priorities after applying updates.
- `uv run aijournal ops profile interview --date YYYY-MM-DD` — generate follow-up questions for that day’s entries.
- `uv run aijournal export pack --level L4 --date YYYY-MM-DD --history-days N --format json` — build a long-horizon pack for external assistants.
- `uv run aijournal ops system ollama health` — verify available models on the Ollama host.

---

## 7. Quick Reference Flow

```
init → capture (--text/--from) → status
   ↓
chat / advise / export pack / serve chat
   ↓
ops feedback apply (as needed)
```

If you need to inspect individual stages, re-run them via `aijournal ops ...`; otherwise `capture`
keeps derived artifacts refreshed automatically.

---

## 8. Developer Notes

The runtime is now split between small, testable modules:

- `src/aijournal/commands/` handles orchestration for each Typer command—file system inputs/outputs, retries, and user messaging live here.
- `src/aijournal/pipelines/` contains deterministic workflows that combine services and prompts (summaries, facts, persona, packs, characterize, advise). Pipelines never touch Typer directly, making them easy to unit test.
- `src/aijournal/services/` keeps reusable integrations (Ollama client, retriever, chat API, feedback).

If you need to extend a command, start with the relevant `commands/*.py` module and only dip into pipelines/services when you need new orchestration steps. Keep CLI changes limited to wiring so the high-level flow in this guide stays stable.

---

Keep this workflow handy whenever you add new entries or revisit older notes. Once you’re comfortable with the ordering, you can automate sections (e.g., a daily script) or integrate the commands into your own tooling. Happy journaling!
