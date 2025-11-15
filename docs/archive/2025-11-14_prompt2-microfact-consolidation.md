## Prompt 2 – RAG + ChromaDB for microfact consolidation (avoid duplicates, strengthen recurring facts)

```text
You are working in the `aijournal` repository.

High-level goal
---------------
Make microfacts truly incremental and consolidated over time, instead of accumulating duplicates.

Right now, the extract-facts pipeline can emit microfacts that are conceptually identical across days (e.g., a recurring habit) without any consolidation step. We want a RAG-based flow that, whenever new microfacts are generated, looks up similar existing microfacts and either:

- Strengthens / updates an existing fact (e.g., higher confidence, extended last_seen), OR
- Adds a new fact when the meaning is genuinely different.

We explicitly want to use **RAG over microfacts backed by ChromaDB**.

Context (what exists now)
-------------------------
- Stage 3 (`aijournal ops pipeline extract-facts --date YYYY-MM-DD`) uses `prompts/extract_facts.md`:
  - Input: normalized entries (`ENTRIES_JSON`).
  - Output: `derived/microfacts/YYYY-MM-DD.yaml` with:
    - `facts`: `{id, statement, confidence, evidence_entry, evidence_para, first_seen, last_seen}`
    - `claim_proposals`: optional claim inputs.
  - There is also an internal “consolidation preview” that converts microfacts into claim proposals, but it’s still basically day-scoped.
- There is *no* global, cross-day microfact index dedicated to consolidation.
- The existing retrieval stack (Annoy + SQLite) indexes chunks of normalized text, not microfacts.

Design constraints
------------------
- Microfacts are intended to be small, evidence-backed, atomic statements.
- We want to maintain both:
  - Per-day microfact artifacts (for audit and reproducibility).
  - Some global view of consolidated microfacts (for reasoning and persona support).
- We have a large context window and we’re comfortable spending extra compute at extract-facts time.
- Use ChromaDB as the vector store for microfacts (local, embedded in the workspace).
  - It’s okay to reuse the same embeddings the project already uses (Ollama + embeddinggemma), but you can decide the best way to integrate.
- Backward compatibility is explicitly out of scope; there are no users to support. Prefer deleting or rewriting legacy code instead of preserving or deprecating it, and never add compatibility shims or feature flags that keep obsolete behavior alive.

What to implement
-----------------
1. Introduce a ChromaDB-backed microfact index
   - Create a small service layer that:
     - Stores microfacts as vectors in ChromaDB.
     - Uses the microfact `statement` (and possibly key metadata) as the text to embed.
     - Associates each vector with:
       - A stable microfact ID (or a composite key).
       - Metadata: `evidence_entry`, `first_seen`, `last_seen`, type/domain if available, etc.
   - The index should live in the workspace (e.g., under `derived/`), and be rebuildable from the YAML artifacts if needed.

2. RAG lookup when new microfacts are generated
   - When `extract-facts` produces the per-day microfacts for date D:
     - For each new microfact:
       - Compute its embedding.
       - Query ChromaDB for the top-k similar microfacts (k is configurable).
       - Restrict to same or compatible domain/scope when possible (you decide what “compatible” means based on available metadata).
   - With this RAG result, implement a decision procedure:
     - If a retrieved microfact is semantically the same as the new one:
       - Treat the new evidence as strengthening/continuing the existing microfact.
       - Update the existing microfact’s:
         - `confidence` (e.g., via some deterministic rule).
         - `last_seen` (and possibly `first_seen` if needed).
     - If the new microfact is genuinely different in meaning:
       - Keep it as a new microfact.
   - You can optionally involve the LLM in deciding “same vs different” if needed:
     - E.g., simple brief prompt: “Given existing fact A and candidate B, are they semantically the same or meaningfully different?”
     - But keep the overall flow deterministic and reproducible as much as possible.

3. Represent consolidated microfacts explicitly
   - Keep the existing per-day `derived/microfacts/YYYY-MM-DD.yaml` untouched for audit.
   - Add a global or incremental artifact representing consolidated microfacts, for example:
     - `derived/microfacts/global.yaml` or per-shard versions.
   - This view should:
     - Track `first_seen` and `last_seen` across all days.
     - Maintain a meaningful `confidence` that increases with repeated evidence.
   - Ensure we can rebuild this consolidated view from scratch (microfact YAML + ChromaDB) if needed.

4. Make this feed into the rest of the system later
   - In this task, just focus on:
     - Building the microfact RAG index.
     - Using it at extract-time to avoid naive duplication.
     - Producing a consolidated view.
   - But design it so that later we can:
     - Use consolidated microfacts as input to profile_update/characterize.
     - Index microfacts for chat/advise retrieval.

Why we’re doing this
--------------------
- Without consolidation, repeated behaviors (e.g., the same focus habit across many days) create many near-identical microfacts.
  - This clutters the evidence layer.
  - It makes downstream reasoning noisier and less explainable.
- We want microfacts to be the canonical, incremental evidence layer:
  - Repeated events should strengthen existing facts, not spawn unbounded duplicates.
  - New nuances should create new facts only when they genuinely add meaning.
- RAG + ChromaDB is a good fit:
  - We retrieve similar microfacts by semantics.
  - We use them to decide “merge vs new fact”.
  - Everything remains local-first and inspectable.
```
