SELECT
  p.repo,
  p.task_type,
  e.model,
  e.variant,
  e.reasoning,
  COUNT(*) AS executions,
  ROUND(AVG(e.total_tokens),1) AS avg_total_tokens,
  ROUND(AVG(e.input_tokens),1) AS avg_input_tokens,
  ROUND(AVG(e.output_tokens),1) AS avg_output_tokens,
  ROUND(AVG(e.cached_tokens),1) AS avg_cached_tokens,
  ROUND(AVG(e.tool_calls),1) AS avg_tool_calls,
  ROUND(AVG(e.duration_seconds),1) AS avg_duration_seconds,
  SUM(CASE WHEN e.result='PASS' THEN 1 ELSE 0 END) AS passes
FROM executions e
JOIN prompts p ON p.prompt_uid=e.prompt_uid
GROUP BY p.repo,p.task_type,e.model,e.variant,e.reasoning
ORDER BY avg_total_tokens ASC, avg_tool_calls ASC;
