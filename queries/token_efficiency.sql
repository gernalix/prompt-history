-- Cost efficiency at task granularity: one sample per PROMPT_ID.
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
    COUNT(DISTINCT reasoning) AS reasoning_count,
    SUM(COALESCE(input_tokens,0)) AS input_tokens,
    SUM(COALESCE(output_tokens,0)) AS output_tokens,
    SUM(COALESCE(cached_tokens,0)) AS cached_tokens,
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
    x.*,
    CASE WHEN x.detailed_cycles>0 THEN x.detailed_tokens ELSE x.fallback_total_tokens END AS total_tokens,
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
  ROUND(AVG(s.total_tokens),1) AS avg_total_tokens,
  ROUND(AVG(s.input_tokens),1) AS avg_input_tokens,
  ROUND(AVG(s.output_tokens),1) AS avg_output_tokens,
  ROUND(AVG(s.cached_tokens),1) AS avg_cached_tokens,
  ROUND(AVG(s.tool_calls),1) AS avg_tool_calls,
  ROUND(AVG(s.duration_seconds),1) AS avg_duration_seconds,
  SUM(CASE WHEN s.result='PASS' THEN 1 ELSE 0 END) AS passes
FROM sample s
JOIN prompts p ON p.prompt_uid=s.prompt_uid
GROUP BY p.repo,p.task_type,s.model,s.variant,s.reasoning
ORDER BY avg_total_tokens ASC, avg_tool_calls ASC;
