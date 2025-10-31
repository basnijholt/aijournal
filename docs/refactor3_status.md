# Refactor3 Execution Status

## Decision Log

| Date | Step | Decision | Impact |
| ---- | ---- | -------- | ------ |
| 2025-10-29 | Stage 3 | Swapped legacy response models for strict domain schemas; updated prompts, tests, and schema snapshots. | Pipelines now emit/validate `DailySummary`, `MicroFactsFile`, and `ProfileUpdateProposals`; Stage 3 strict-schema milestone marked complete. |
| 2025-10-29 | Stage 4 | Moved persona/interview models into `aijournal.domain.persona` and rewired consumers; blessed new schemas. | Persona artifacts now share the strict domain layer, CLI/chat imports are unified, and schema governance tracks the new files. |
| 2025-10-29 | Stage 5 | Domainized claim preview/feedback events and introduced strict feedback batches. | Consolidation previews and chat feedback now emit discriminated events (`preview`/`feedback`); CI consumes the new schemas end to end. |
| 2025-10-29 | Stage 8.1a | Wrapped daily summaries and micro-facts in `Artifact[T]` envelopes; updated CLI/tests/docs. | Derived context now carries deterministic envelopes; doc examples refreshed. |
| 2025-10-29 | Stage 8.1b | Persona core, profile proposals, pending update batches, and feedback files now emit artifact envelopes. | All persona/profile derived artifacts share the strict structure; CLI and capture tests consume the envelopes. |
| 2025-10-29 | Stage 8.1c | Packs and chat transcripts now persist as `Artifact[T]` envelopes; CLI/tests/docs refreshed. | Pack exports gain `kind/meta` headers and chat transcripts write deterministic JSON artifacts in `derived/chat_sessions/`. |
| 2025-10-30 | Stage 3 | Replaced the remaining chat dataclasses with strict domain models and propagated telemetry updates through CLI/API/tests. | ChatService returns `ChatTurn`/`ChatTelemetry` (StrictModel) objects end-to-end, eliminating the legacy wrappers. |
| 2025-10-30 | Stage 8.1d | Wrapped advice cards plus chat summaries/learnings in artifact envelopes with new domain schemas; refreshed fixtures/tests. | Advice outputs and chat session rollups now share deterministic `Artifact[T]` envelopes and typed payloads. |
| 2025-10-30 | Stage 8.2 | Eliminated legacy pending-batch readers and enforced strict artifact loads across advise/chat surfaces. | All CLI/services now load profile proposals/updates via `Artifact[T]`; malformed legacy files raise guidance errors instead of being auto-coerced. |
| 2025-10-30 | Stage 8.3 | Added `aijournal ops audit provenance [--fix]` with recursive span scanning and redaction helpers. | CLI audit reports/fixes persisted `span.text` remnants across claims, persona, and profile update artifacts. |
| 2025-10-30 | Stage 8.4 | Codemod deemed unnecessary; removed from runbook. | Legacy imports were already purged, so no automated rewriting tool is needed. |
| 2025-10-30 | Stage 9 | Updated operator docs (`AGENTS.md`, workflow) to reference strict artifacts & audit flow; refreshed example fixtures remain aligned with envelopes. | Docs now point to the new governance command and confirm envelope-based examples; Stage 9 checklist marked complete. |
| 2025-10-30 | Stage 10 | Ran fake-mode rehearsal (`capture`, pipelines, chat/advise, audit) in a fresh workspace and recorded the outcome. | End-to-end flow succeeds without legacy paths; audit confirms no persisted span text. |
| 2025-10-30 | Post-Cutover Cleanup | Promoted chat citations to typed payloads across CLI/API/tests. | Chat transcripts and streaming responses now emit structured citation objects; schema snapshots/tests enforce the contract. |
| 2025-10-30 | Post-Cutover Cleanup | Replaced chunk manifests with `ArtifactKind.INDEX_CHUNKS` chunk batches and added regression coverage. | Retrieval exports now reuse domain index models, docs/schemas/tests align, and legacy manifest models are gone. |
| 2025-10-30 | Post-Cutover Cleanup | Aligned claim preview action vocabulary with the refactor3 enum. | Consolidator outputs, CLI previews, and schema snapshots now use `upsert/update/delete/conflict/strength_delta`, eliminating legacy action codes. |
| 2025-10-30 | Post-Cutover Cleanup | Normalized ingest timestamps to ISO strings across capture/ingest flows. | `IngestResult` now carries strings, fake ingest paths emit ISO timestamps, and normalization/tests were updated accordingly. |
| 2025-10-30 | Post-Cutover Cleanup | Removed persona/interview double-meta payloads in favor of artifact metadata. | Persona artifacts persist `Artifact[PersonaCore]` with metadata in `ArtifactMeta`; interview sets dropped inner `SummaryMeta`, and docs/schemas/examples were refreshed. |
| 2025-10-30 | Post-Cutover Cleanup | Audited domain models for mutable defaults. | Confirmed all strict models rely on `Field(default_factory=...)`; schema snapshots guard against regressions. |

## Stage 8 Execution Plan (Artifact Adoption)

To keep Stage 8 reviewable we will convert each artifact family in a dedicated commit with green tests:

1. **Stage 8.1a – Daily summaries & microfacts** → wrap `derived/summaries/*.yaml` and `derived/microfacts/*.yaml` in `Artifact[T]`, update pipelines/tests/fixtures.
2. **Stage 8.1b – Persona & profile updates** → convert persona core, profile proposals/updates, feedback batches.
3. **Stage 8.1c – Packs & chat transcripts** → emit `Artifact[T]` envelopes for pack exports and chat session transcripts.
4. **Stage 8.1d – Remaining artifacts** → sweep advice cards, capture logs, and any stragglers; ensure deterministic serialization everywhere.
5. **Stage 8.3 – Provenance audit command** → implement `aijournal ops audit provenance [--fix]` after envelopes are in place.
6. **Stage 8.4 – Import codemod** → add the LibCST codemod once the new module paths are finalised.

We intentionally skip a compatibility layer (Stage 8.2) because refactor3 runs in a repo without external users; all tooling adopts the strict envelopes immediately.
