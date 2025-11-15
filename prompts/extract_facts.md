You analyze normalized journal entries and produce micro-facts (plus optional claim proposals) as structured JSON.
Return JSON with exactly the keys `facts` and `claim_proposals` (omit the claim array when empty).

```
{
  "facts": [
    {
      "id": "kebab-case identifier",
      "statement": "Atomic observation",
      "confidence": 0.0-1.0,
      "evidence_entry": "id from input entries",
      "evidence_para": <paragraph index>,
      "first_seen": "YYYY-MM-DD",
      "last_seen": "YYYY-MM-DD"
    }
  ],
  "claim_proposals": [
    {
      "type": "preference|value|goal|boundary|trait|habit|aversion|skill",
      "statement": "Readable sentence (≤160 chars)",
      "subject": "optional subject (≤80 chars)",
      "predicate": "optional predicate (≤80 chars)",
      "value": "optional value (≤160 chars)",
      "strength": 0.0-1.0 (optional),
      "status": "accepted|tentative|rejected" (optional),
      "method": "self_report|inferred|behavioral" (optional),
      "scope_domain": "optional domain",
      "scope_context": ["optional tags"],
      "scope_conditions": ["optional qualifiers"],
      "reason": "≤25 word justification",
      "evidence_entry": "normalized-entry-id",
      "evidence_para": 0
    }
  ]
}
```

## Daily Summary (read first)

Treat the daily summary as a map of what mattered most before you dive into the
full entries. Use it to seed hypotheses, then confirm every statement against
the normalized text. Never emit a fact that contradicts the summary or the
entries.

SUMMARY_JSON:
$summary_json

Guidelines:
- Use every structured field available. Pull supporting details from `summary`, `sections`, and `tags`; when paragraphs are absent, synthesize the most concrete statement implied by those fields.
- Facts must be specific, non-trivial statements grounded in the provided entries. Never contradict the source metadata.
- Treat figurative or poetic language as context only. Only emit immutable biographical facts when the entry states them literally and unambiguously; timestamps or stylistic markers alone never prove a factual event.
- Before asserting any fact, reread the paragraph to confirm it describes reality rather than feelings, analogies, or rhetorical flourishes. When the meaning is ambiguous, drop the fact or lower the confidence instead of inventing a literal claim.
- Reference entry IDs exactly as supplied.
- Confidence reflects evidence strength (default 0.6 if unsure).
- Reuse the entry's `created_at` date for `first_seen`/`last_seen` when only one mention exists.
- Claim proposals are optional; emit them only when you can cite specific evidence that maps to a persona insight.
- Do not include analysis or prose outside JSON.
If you cannot produce a valid payload matching this schema, respond with `{"facts": [], "claim_proposals": []}` as
the full response.
See `prompts/examples/extract_facts.json` for a concrete example.

DATE: $date

ENTRIES_JSON:
$entries_json
