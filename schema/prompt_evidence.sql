PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

INSERT INTO meta(key, value) VALUES('schema_version', '1')
ON CONFLICT(key) DO UPDATE SET value=excluded.value;

CREATE TABLE IF NOT EXISTS source_records (
  source TEXT NOT NULL,
  source_key TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  kind TEXT NOT NULL,
  ingested_at TEXT NOT NULL,
  PRIMARY KEY (source, source_key, payload_hash)
);

CREATE TABLE IF NOT EXISTS prompts (
  prompt_uid TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  source_key TEXT NOT NULL,
  prompt_id TEXT,
  conversation_id TEXT,
  message_id TEXT,
  parent_prompt_uid TEXT,
  role TEXT,
  title TEXT,
  prompt_text TEXT NOT NULL DEFAULT '',
  prompt_hash TEXT,
  text_quality INTEGER NOT NULL DEFAULT 0,
  project_id TEXT,
  repo TEXT,
  task_type TEXT,
  created_at TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  inserted_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_prompts_prompt_id
ON prompts(prompt_id) WHERE prompt_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_prompts_source_key ON prompts(source, source_key);
CREATE INDEX IF NOT EXISTS idx_prompts_project_repo ON prompts(project_id, repo);
CREATE INDEX IF NOT EXISTS idx_prompts_task_type ON prompts(task_type);
CREATE INDEX IF NOT EXISTS idx_prompts_created_at ON prompts(created_at);

CREATE TABLE IF NOT EXISTS executions (
  execution_uid TEXT PRIMARY KEY,
  prompt_uid TEXT NOT NULL REFERENCES prompts(prompt_uid) ON DELETE CASCADE,
  source TEXT NOT NULL,
  source_key TEXT NOT NULL,
  session_id TEXT,
  model TEXT,
  variant TEXT,
  reasoning TEXT,
  duration_seconds REAL,
  input_tokens INTEGER,
  output_tokens INTEGER,
  cached_tokens INTEGER,
  total_tokens INTEGER,
  tool_calls INTEGER,
  result TEXT,
  blocker_class TEXT,
  acceptance_passed INTEGER CHECK (acceptance_passed IN (0,1) OR acceptance_passed IS NULL),
  started_at TEXT,
  ended_at TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  inserted_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_executions_prompt ON executions(prompt_uid);
CREATE INDEX IF NOT EXISTS idx_executions_model ON executions(model, variant, reasoning);
CREATE INDEX IF NOT EXISTS idx_executions_result ON executions(result);
CREATE INDEX IF NOT EXISTS idx_executions_time ON executions(started_at, ended_at);

CREATE TABLE IF NOT EXISTS relations (
  relation_uid TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  source_key TEXT NOT NULL,
  from_prompt_uid TEXT NOT NULL REFERENCES prompts(prompt_uid) ON DELETE CASCADE,
  to_prompt_uid TEXT NOT NULL REFERENCES prompts(prompt_uid) ON DELETE CASCADE,
  relation_type TEXT NOT NULL,
  confidence REAL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_relations_from ON relations(from_prompt_uid, relation_type);
CREATE INDEX IF NOT EXISTS idx_relations_to ON relations(to_prompt_uid, relation_type);

CREATE TABLE IF NOT EXISTS artifacts (
  artifact_uid TEXT PRIMARY KEY,
  prompt_uid TEXT NOT NULL REFERENCES prompts(prompt_uid) ON DELETE CASCADE,
  source TEXT NOT NULL,
  source_key TEXT NOT NULL,
  artifact_type TEXT NOT NULL,
  artifact_key TEXT NOT NULL,
  status TEXT,
  url TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT,
  inserted_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_artifacts_prompt ON artifacts(prompt_uid);
CREATE INDEX IF NOT EXISTS idx_artifacts_type ON artifacts(artifact_type, status);

CREATE VIRTUAL TABLE IF NOT EXISTS prompt_fts USING fts5(
  prompt_uid UNINDEXED,
  title,
  prompt_text,
  repo,
  task_type,
  tokenize='unicode61 remove_diacritics 2'
);

DROP VIEW IF EXISTS v_model_performance;
CREATE VIEW v_model_performance AS
SELECT
  e.model,
  e.variant,
  e.reasoning,
  COUNT(*) AS executions,
  SUM(CASE WHEN e.result='PASS' THEN 1 ELSE 0 END) AS pass_count,
  SUM(CASE WHEN e.result='BLOCKED' THEN 1 ELSE 0 END) AS blocked_count,
  SUM(CASE WHEN e.result='FAIL' THEN 1 ELSE 0 END) AS fail_count,
  ROUND(1.0 * SUM(CASE WHEN e.result='PASS' THEN 1 ELSE 0 END) / COUNT(*), 4) AS pass_rate,
  ROUND(AVG(e.total_tokens), 1) AS avg_total_tokens,
  ROUND(AVG(e.tool_calls), 1) AS avg_tool_calls,
  ROUND(AVG(e.duration_seconds), 1) AS avg_duration_seconds
FROM executions e
GROUP BY e.model, e.variant, e.reasoning;

DROP VIEW IF EXISTS v_blockers;
CREATE VIEW v_blockers AS
SELECT
  COALESCE(e.blocker_class, 'unclassified') AS blocker_class,
  COUNT(*) AS occurrences,
  COUNT(DISTINCT e.prompt_uid) AS prompts,
  MIN(e.started_at) AS first_seen_at,
  MAX(COALESCE(e.ended_at, e.started_at)) AS last_seen_at
FROM executions e
WHERE e.result='BLOCKED'
GROUP BY COALESCE(e.blocker_class, 'unclassified');
