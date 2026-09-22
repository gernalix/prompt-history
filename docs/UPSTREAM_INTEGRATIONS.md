# Upstream integrations

`prompt-history` is a derived warehouse. Capture/parsing belongs to maintained
upstream projects when they already solve the provider-specific problem well.

## Pinned upstreams

| Source | Upstream | Revision | License | Responsibility |
| --- | --- | --- | --- | --- |
| ChatGPT Web | `siraht/ChatGPTExporter` | `c5618b3cc06eeb5b273d3727fe8729071441f291` | MIT | Authenticated, resumable Web inventory/capture; full conversation graph and assets |
| Codex Desktop/local sessions | `janole/session-bandit` | `a618b5b54d804eae8d2537feec77e2d2acdfe3e8` (v0.1.7) | MIT | Parse legacy JSON, flat JSONL and modern envelope JSONL Codex sessions |

These revisions were verified on 2026-09-22. Updating a pin requires targeted
compatibility tests before runtime cutover.

## ChatGPT Web

Do not copy ChatGPT private endpoints or authentication into `prompt-history`.
Build/run ChatGPTExporter separately and point `ingest-chatgpt-exporter` (or
`sync --chatgpt-exporter`) at an audited export directory. The adapter consumes:

```text
ChatGPTExport-*/
  conversations/<conversation-id>/conversation.json
```

It preserves conversation/message identity, parent relations, title, timestamp,
model slug, workspace fingerprint and membership metadata. It intentionally
uses the same `chatgpt:<conversation>:<message>` identity as the official
`conversations.json` adapter so the two sources converge rather than fork.

The OpenAI `conversations.json` adapter remains supported as an independent
fallback. Never require a fresh Web scrape when an authoritative export already
contains the needed history.

## Codex Desktop/local sessions

Session Bandit's core package is built from the pinned source checkout rather
than vendored here. After building that checkout, emit normalized sessions:

```bash
node tools/session_bandit_dump.mjs \
  ~/.local/share/prompt-history/upstream/session-bandit/packages/core/dist/index.js \
  ~/.codex/sessions \
  > ~/.local/share/prompt-history/session-bandit-codex.jsonl

python3 -m prompt_history.cli ingest-session-bandit \
  --db ~/.local/share/prompt-history/prompt_history.sqlite \
  --input ~/.local/share/prompt-history/session-bandit-codex.jsonl
```

The Session Bandit adapter contributes full normalized transcript/provenance.
It deliberately creates no `executions`: `codex-usage` remains authoritative
for model/reasoning/token/tool-call/duration/result metrics, preventing cost
double-counting.

## Runtime policy

1. Prefer already-present authoritative local data over re-downloading.
2. Keep upstream checkouts outside this repository and pin exact revisions.
3. Keep authentication, cookies, raw transcripts, exports and derived databases
   outside Git.
4. Do not vendor destructive provider operations or credential flows.
5. If an upstream format drifts, add one redacted/synthetic regression fixture,
   update only the narrow adapter, and keep the previous fallback usable.
6. Sync must remain idempotent; repeated unchanged imports must not create
   duplicate logical prompts, relations or executions.
