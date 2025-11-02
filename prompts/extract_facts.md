You analyze normalized journal entries and produce micro-facts as structured JSON.
Return JSON with exactly this structure (keep every property inside each fact object). You only need to emit
the `facts` array; the outer structure is added automatically:

```
{
  "facts": [
    {
      "id": "kebab-case identifier",
      "statement": "Atomic observation",
      "confidence": 0.0-1.0,
      "evidence": {
        "entry_id": "id from input entries",
        "spans": [
          {"type": "para", "index": <paragraph index>}
        ]
      },
      "first_seen": "YYYY-MM-DD",
      "last_seen": "YYYY-MM-DD"
    }
  ]
}
```

Guidelines:
- Use every structured field available. Pull supporting details from `summary`, `sections`, and `tags`; when paragraphs are absent, synthesize the most concrete statement implied by those fields.
- Facts must be specific, non-trivial statements grounded in the provided entries. Never contradict the source metadata.
- Reference entry IDs exactly as supplied.
- Confidence reflects evidence strength based on quality and specificity of the supporting details.
- Reuse the entry's `created_at` date for `first_seen`/`last_seen` when only one mention exists.
- Do not include analysis or prose outside JSON.

**Quality Standards** – Before extracting any fact:
1. Can this be verified by re-reading the journal entry?
2. Would a neutral observer reach the same conclusion?
3. Is there concrete supporting evidence (not just inference)?
4. Does the statement add non-trivial information?

If ANY answer is "no", omit the fact. Return empty arrays when evidence is weak or ambiguous.

If you cannot produce a valid payload matching this schema, respond with `{"facts": []}` as
the full response.
See `prompts/examples/extract_facts.json` for a concrete example.

DATE: $date

ENTRIES_JSON:
$entries_json
