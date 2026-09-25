# prompt-history

Derived, rebuildable evidence warehouse for ChatGPT and Codex prompts.

## Purpose

`prompt-history` normalizes prompt history from existing canonical systems without becoming a competing source of truth. It is designed to answer questions such as:

- Which model/reasoning level has worked best for comparable tasks?
- Which prompt patterns correlate with PASS, BLOCKED, FAIL or RETRY?
- Which blockers recur, and which later prompts actually resolved them?
- Which repositories/task types consume the most tokens, tool calls or wall-clock time?
- What prior ChatGPT prompt generated a Codex prompt, and what happened next?

The warehouse is **derived**. Canonical state remains in the systems that own it (for example `codex-roadmap/roadmap.sqlite`). The local `prompt_history.sqlite` can be deleted and rebuilt from source data.

## Design

Inputs are normalized into stable entities:

- `prompts`: one normalized ChatGPT/Codex prompt/message
- `executions`: model, reasoning, token/tool/duration and result data
- `relations`: generated-by, corrective-follow-up, resolves, supersedes and similar relationships
- `artifacts`: commits, PRs, CI runs, files and tests
- `source_records`: immutable provenance/deduplication ledger
- `prompt_fts`: FTS5 lexical search over normalized prompt text

No canonical source is mutated by this project.

## Supported ingestion

The initial implementation supports:

1. `codex-roadmap/roadmap.sqlite` (prompt materializations, relations, executions, analyses and artifacts)
2. `codex-usage/index/prompts.jsonl` plus per-cycle `metrics.json`
3. OpenAI/ChatGPT export `conversations.json`
4. audited `siraht/ChatGPTExporter` archives (`conversations/*/conversation.json`)
5. normalized Codex transcripts produced by `janole/session-bandit`
6. normalized JSONL records for additional adapters/exporters

ChatGPT messages containing literal `PROMPT_ID=<6 digits>` markers are linked deterministically to the corresponding Codex prompt. Assistant messages become `generated` relations, user messages become `references_prompt`, and literal `PARENT_PROMPT_ID` + `PROMPT_ID` pairs become Codex parent relations. No semantic/guess-based linking is performed.

## Quick start

```bash
python3 -m prompt_history.cli init --db ./prompt_history.sqlite

python3 -m prompt_history.cli ingest-roadmap \
  --db ./prompt_history.sqlite \
  --roadmap ~/projects/codex-roadmap/roadmap.sqlite

python3 -m prompt_history.cli ingest-codex-usage \
  --db ./prompt_history.sqlite \
  --repo ~/projects/codex-usage

python3 -m prompt_history.cli ingest-chatgpt \
  --db ./prompt_history.sqlite \
  --conversations ~/Downloads/chatgpt-export/conversations.json

python3 -m prompt_history.cli ingest-chatgpt-exporter \
  --db ./prompt_history.sqlite \
  --archive ~/Documents/ChatGPT/ChatGPTExport-workspace

node tools/session_bandit_dump.mjs \
  ~/.local/share/prompt-history/upstream/session-bandit/packages/core/dist/index.js \
  ~/.codex/sessions > ~/.local/share/prompt-history/session-bandit-codex.jsonl
python3 -m prompt_history.cli ingest-session-bandit \
  --db ./prompt_history.sqlite \
  --input ~/.local/share/prompt-history/session-bandit-codex.jsonl

python3 -m prompt_history.cli link --db ./prompt_history.sqlite

python3 -m prompt_history.cli ingest-jsonl \
  --db ./prompt_history.sqlite \
  --input /path/to/normalized.jsonl

python3 -m prompt_history.cli similar \
  --db ./prompt_history.sqlite \
  --text "fix Android navigation bug" \
  --limit 10

python3 -m prompt_history.cli model-stats \
  --db ./prompt_history.sqlite \
  --repo PersonalHub

python3 -m prompt_history.cli recommend \
  --db ./prompt_history.sqlite \
  --repo PersonalHub \
  --task-type Prompt \
  --min-samples 3
```

## Upstream-first extraction

`prompt-history` does not reimplement provider scraping/parsing when a suitable
audited open-source implementation exists. The supported upstream path is:

- ChatGPT Web capture: `siraht/ChatGPTExporter`, pinned and validated separately;
  this project reads its normalized `conversation.json` artifacts. The official
  OpenAI `conversations.json` export remains a fallback.
- Codex session parsing: `janole/session-bandit`; the small bridge under
  `tools/session_bandit_dump.mjs` delegates the three historical Codex rollout
  formats to Session Bandit's adapter. `codex-usage` remains authoritative for
  execution/token/tool-call metrics, so transcript ingestion does not create
  duplicate execution rows.

Exact revisions, licenses, trust boundaries and runtime setup are documented in
`docs/UPSTREAM_INTEGRATIONS.md`.

## Normalized JSONL contract

Each line is one object with `kind` equal to `prompt`, `execution`, `relation` or `artifact`. Example:

```json
{"kind":"prompt","source":"codex","source_key":"593872","prompt_id":"593872","prompt_text":"...","project_id":"49","repo":"PersonalHub","task_type":"android_localized_fix","created_at":"2026-09-21T20:00:00+02:00"}
{"kind":"execution","source":"codex-usage","source_key":"session-abc","prompt_id":"593872","model":"GPT-5.6","variant":"Terra","reasoning":"medium","input_tokens":12000,"output_tokens":2400,"tool_calls":19,"duration_seconds":820,"result":"BLOCKED","blocker_class":"claim_rejected"}
{"kind":"relation","source":"linker","source_key":"rel-1","from_prompt_id":"593872","to_prompt_id":"712491","relation_type":"resolved_by"}
{"kind":"artifact","source":"github","source_key":"pr-15","prompt_id":"712491","artifact_type":"pull_request","artifact_key":"gernalix/repo#15","status":"merged"}
```

## Rebuild policy

The database is not committed. To rebuild it:

```bash
rm -f prompt_history.sqlite
python3 -m prompt_history.cli init --db prompt_history.sqlite
# rerun the configured ingesters
```

Every ingested source row is deduplicated through `(source, source_key, payload_hash)`, so rerunning an ingester is safe.

## Fedora runtime

The derived database is installed at `~/.local/share/prompt-history/prompt_history.sqlite`.
Versioned units live under `systemd/` and split cheap/frequent work from expensive
transcript normalization:

- `prompt-history-sync.timer` runs about every 15 minutes and reads roadmap,
  codex-usage, all `ChatGPTExport-*` archives below `~/Documents/ChatGPT`, and
  the read-only switcher database. ChatGPT files are content-hash checkpointed, so
  unchanged conversations are skipped before JSON parsing.
- `prompt-history-transcripts.timer` runs about hourly and refreshes the much
  heavier Session Bandit Codex transcript bridge.
- Both services share one local `flock` so they cannot write the derived SQLite
  database concurrently.

Passing the parent ChatGPT directory is intentional: the adapter discovers one or more
normalized exporter archives and deduplicates them by canonical source keys. A rebuild
removes only the derived database and reruns the configured ingesters; no upstream source
is changed.

## Analytics

Reusable SQL lives under `queries/`:

- `similar_prompts.sql`
- `model_performance.sql`
- `blocker_analysis.sql`
- `token_efficiency.sql`

The CLI exposes similarity search, model statistics, blocker summaries and a bounded model/reasoning recommender. Cost/model analytics use **one sample per canonical PROMPT_ID**, not one sample per Codex turn: raw cycle executions remain available for audit, but ranking uses only the authoritative `codex-usage` execution metrics, aggregates per-cycle token/tool/duration cost per prompt, ignores mirrored roadmap execution rows, and excludes prompts that changed model/reasoning mid-task. This prevents long `/goal` continuations and cumulative goal totals from being counted multiple times. The recommender returns no selection when the configured minimum sample count is not met. It ranks only observed combinations and always returns the evidence row used for the recommendation.

When a roadmap analysis names a fix prompt and that fix prompt has a recorded PASS execution, ingestion also materializes a `resolved_by` relation. A mere fix relation without PASS remains only `fix_prompt`.

## Repository boundaries

`prompt-history` owns normalization, provenance, linking and analytics. It does **not** own:

- Codex roadmap lifecycle
- Workflowy operational state
- browser tab state
- Codex raw session capture
- GitHub CI/PR state

Those remain in their existing repositories and are ingested read-only.
