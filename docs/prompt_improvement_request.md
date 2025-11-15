# Prompt Improvement Request

You are an expert prompt engineer tasked with improving the `aijournal` CLI prompts. You have access to the following materials:

1. `docs/prompt_evaluation_report.md` – detailed findings from the latest capture run, including successes, failure modes, and per-prompt examples.
2. `ARCHITECTURE.md` – system design, persona/memory layers, pipelines, and schema guarantees.
3. `docs/workflow.md` – operator workflow, command order, and pipeline expectations.
4. `TLDR.md` – capture pipeline quick reference (stages, inputs, outputs).
5. `README.md` – product overview, goals, and runtime prerequisites.

## Your Tasks
1. For each prompt under `prompts/` (`summarize_day.md`, `extract_facts.md`, `profile_update.md`, `interview.md`, `advise.md`), propose concrete improvements that address the failure modes documented in the report. Include:
   - Specific instruction changes (extra constraints, better examples, reminders of schema contracts).
   - Validation guardrails (evidence span requirements, duplicate suppression, allowed enums/paths, etc.).
   - Any supporting tooling/pipeline adjustments needed for the prompt to operate reliably.
2. Prioritize fixes that unblock downstream stages (profile updates, persona, advice) and explain inter-prompt dependencies where relevant.
3. List open questions or follow-up tests required after revising the prompts.

Deliver your response as a structured plan with headings per prompt plus cross-cutting recommendations. Cite relevant sections/lines in the supplied docs whenever the rationale depends on architectural or workflow decisions.
