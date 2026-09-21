# AGENTS.md

## Scope
This repository is a derived evidence warehouse. Do not turn it into a second source of truth for roadmap state, Workflowy state, browser state, Codex raw capture, or GitHub state.

## Invariants
- Read canonical inputs; never mutate them.
- The local evidence database must be deletable and rebuildable.
- Preserve provenance for every ingested record.
- Prefer deterministic, idempotent ingestion.
- Merge records sharing the same canonical Codex PROMPT_ID.
- Prefer higher-quality canonical prompt text over redacted/partial copies.
- Keep historical evidence; do not rewrite outcomes to fit later conclusions.
- Report sample counts with model/reasoning comparisons.
- No venv. Runtime code uses Python standard library + system SQLite only.
- Do not add network calls to ingestion code unless a task explicitly requires them.
- Do not commit generated SQLite databases or private exports.

## Tests
Run only the targeted standard-library suite unless a change requires more:

\`\`\`bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
\`\`\`

Stop after the relevant acceptance criteria pass. No unrelated cleanup or refactors.
