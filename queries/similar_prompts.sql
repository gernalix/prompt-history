-- Bind :query to an FTS5 query and :limit to the desired result count.
SELECT
  p.prompt_uid,
  p.prompt_id,
  p.source,
  p.title,
  p.repo,
  p.task_type,
  p.created_at,
  snippet(prompt_fts, 2, '[', ']', ' … ', 24) AS snippet,
  bm25(prompt_fts, 2.0, 5.0, 1.5, 1.5) AS lexical_rank
FROM prompt_fts
JOIN prompts p ON p.prompt_uid = prompt_fts.prompt_uid
WHERE prompt_fts MATCH :query
ORDER BY lexical_rank
LIMIT :limit;
