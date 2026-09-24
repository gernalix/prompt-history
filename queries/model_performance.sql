-- One analytical sample per canonical PROMPT_ID.
-- Raw cycle executions remain available for audit, but cost/model comparisons use
-- codex-usage only and aggregate cycle deltas so long /goal continuations are not
-- overweighted and roadmap mirror rows are not double-counted.
WITH codex AS (
  SELECT e.*,
         ROW_NUMBER() OVER (
           PARTITION BY e.prompt_uid
           ORDER BY
             CASE WHEN e.result IS NOT NULL AND e.result<>'UNKNOWN' THEN 0 ELSE 1 END,
             COALESCE(e.ended_at,e.started_at,e.updated_at,e.inserted_at) DESC,
             e.execution_uid DESC
         ) AS result_rank
  FROM executions e
  WHERE e.source='codex-usage'
),
per_prompt AS (
  SELECT
    prompt_uid,
    MIN(model) AS model,
    MIN(variant) AS variant,
    MIN(reasoning) AS reasoning,
    COUNT(DISTINCT model) AS model_count,
    COUNT(DISTINCT COALESCE(reasoning,'')) AS reasoning_count,
    SUM(
      CASE WHEN input_tokens IS NOT NULL OR output_tokens IS NOT NULL
           THEN COALESCE(input_tokens,0)+COALESCE(output_tokens,0)
           ELSE 0 END
    ) AS detailed_tokens,
    SUM(
      CASE WHEN input_tokens IS NOT NULL OR output_tokens IS NOT NULL
           THEN 1 ELSE 0 END
    ) AS detailed_cycles,
    MAX(COALESCE(total_tokens,0)) AS fallback_total_tokens,
    SUM(COALESCE(tool_calls,0)) AS tool_calls,
    SUM(COALESCE(duration_seconds,0)) AS duration_seconds
  FROM codex
  GROUP BY prompt_uid
),
effective_result AS (
  SELECT prompt_uid,result
  FROM codex
  WHERE result_rank=1
),
sample AS (
  SELECT
    x.prompt_uid,
    x.model,
    x.variant,
    x.reasoning,
    CASE WHEN x.detailed_cycles>0 THEN x.detailed_tokens ELSE x.fallback_total_tokens END AS total_tokens,
    x.tool_calls,
    x.duration_seconds,
    r.result
  FROM per_prompt x
  JOIN effective_result r USING(prompt_uid)
  WHERE x.model_count=1 AND x.reasoning_count<=1 AND x.model IS NOT NULL
)
SELECT
  p.repo,
  p.task_type,
  s.model,
  s.variant,
  s.reasoning,
  COUNT(*) AS prompts,
  COUNT(*) AS executions,
  SUM(CASE WHEN s.result='PASS' THEN 1 ELSE 0 END) AS pass_count,
  SUM(CASE WHEN s.result='BLOCKED' THEN 1 ELSE 0 END) AS blocked_count,
  SUM(CASE WHEN s.result='FAIL' THEN 1 ELSE 0 END) AS fail_count,
  ROUND(1.0 * SUM(CASE WHEN s.result='PASS' THEN 1 ELSE 0 END) / COUNT(*), 4) AS pass_rate,
  ROUND(AVG(s.total_tokens), 1) AS avg_total_tokens,
  ROUND(AVG(s.tool_calls), 1) AS avg_tool_calls,
  ROUND(AVG(s.duration_seconds), 1) AS avg_duration_seconds
FROM sample s
JOIN prompts p ON p.prompt_uid=s.prompt_uid
GROUP BY p.repo,p.task_type,s.model,s.variant,s.reasoning
ORDER BY p.repo,p.task_type,pass_rate DESC,avg_total_tokens ASC;
