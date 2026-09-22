#!/usr/bin/env node
/**
 * Emit normalized Codex sessions as JSONL using Session Bandit's upstream parser.
 *
 * Usage:
 *   node tools/session_bandit_dump.mjs /path/to/session-bandit/packages/core/dist/index.js [codex-root]
 *
 * Upstream is intentionally external/pinned. This repository does not vendor its
 * Codex parser; see docs/UPSTREAM_INTEGRATIONS.md.
 */
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const [, , coreModule, codexRoot] = process.argv;
if (!coreModule) {
  console.error("usage: session_bandit_dump.mjs <session-bandit-core-dist-index.js> [codex-root]");
  process.exit(2);
}

const core = await import(pathToFileURL(resolve(coreModule)).href);
if (typeof core.indexSessions !== "function" || !core.codexAdapter) {
  throw new Error("Session Bandit core module does not expose indexSessions/codexAdapter");
}

const config = codexRoot
  ? [{ adapter: core.codexAdapter, root: resolve(codexRoot) }]
  : [{ adapter: core.codexAdapter }];

for (const session of core.indexSessions(config)) {
  process.stdout.write(JSON.stringify(session) + "\n");
}
