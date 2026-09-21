# Data model and evidence rules

## Canonical identity

Codex prompts with a six-digit \`PROMPT_ID\` always normalize to:

\`\`\`
codex:<PROMPT_ID>
\`\`\`

This lets roadmap data and codex-usage metrics converge on the same prompt even though they originate from different repositories.

ChatGPT messages normalize to:

\`\`\`
chatgpt:<conversation_id>:<message_id>
\`\`\`

Codex cycles without a known \`PROMPT_ID\` use:

\`\`\`
codex-cycle:<cycle_key>
\`\`\`

They can later be connected or merged by a verified adapter without rewriting the canonical sources.

## Text precedence

Every prompt row has \`text_quality\`.

Typical values:

- 100: canonical roadmap materialization or direct ChatGPT export text
- 65: roadmap title/explanation fallback
- 40: redacted Codex usage text
- 0: placeholder created only so an execution/relation can retain referential integrity

A lower-quality ingest never replaces higher-quality text.

## Provenance and idempotency

Every normalized input first enters \`source_records\` using:

\`\`\`
(source, source_key, sha256(canonical_payload))
\`\`\`

Re-ingesting the exact same source payload is a no-op. If a source record legitimately changes, the new payload hash is retained as a new provenance observation and the normalized row is updated.

## Relations

The relation graph can represent:

- \`conversation_parent\`
- \`parent\`
- \`followup\`
- \`fix\`
- \`fix_prompt\`
- \`resolved_by\`
- \`replacement\`
- \`superseded_by\`
- \`depends_on\`
- \`related\`

The schema deliberately does not hard-code a CHECK constraint on relation types, because source systems may add new factual relationships over time.

## Analytics policy

Historical metrics are evidence, not certainty.

Any model/reasoning recommendation must:

1. expose its sample count;
2. prefer comparable repo/task-type history when available;
3. avoid selecting a winner when the configured minimum sample count is not met;
4. retain BLOCKED and FAIL outcomes rather than silently filtering them out;
5. keep token/tool/duration fields separate so efficiency trade-offs remain inspectable.

## Rebuildability

\`prompt_history.sqlite\` is derived and ignored by Git.

A full rebuild is the expected recovery path. Do not hand-edit derived rows to repair canonical history; fix the adapter or the upstream canonical source and rebuild.
