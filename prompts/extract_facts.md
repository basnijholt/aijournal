You analyze normalized journal entries and produce micro-facts suitable for downstream agents.
Return JSON with exactly this structure (keep every property inside each fact object):

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
- Confidence reflects evidence strength (default 0.6 if unsure).
- Reuse the entry's `created_at` date for `first_seen`/`last_seen` when only one mention exists.
- Do not include analysis or prose outside JSON.

DATE: $date

ENTRIES_JSON:
$entries_json
