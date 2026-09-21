-- Descriptive evidence. Always interpret together with executions/sample size.
SELECT
  p.repo,
  p.task_type,
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
JOIN prompts p ON p.prompt_uid=e.prompt_uid
GROUP BY p.repo,p.task_type,e.model,e.variant,e.reasoning
ORDER BY p.repo,p.task_type,pass_rate DESC,avg_total_tokens ASC;
