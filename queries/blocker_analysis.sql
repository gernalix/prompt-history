SELECT
  COALESCE(e.blocker_class, 'unclassified') AS blocker_class,
  COUNT(*) AS occurrences,
  COUNT(DISTINCT e.prompt_uid) AS affected_prompts,
  SUM(CASE WHEN r.relation_uid IS NOT NULL THEN 1 ELSE 0 END) AS prompts_with_recorded_fix_relation,
  MIN(e.started_at) AS first_seen_at,
  MAX(COALESCE(e.ended_at,e.started_at)) AS last_seen_at
FROM executions e
LEFT JOIN relations r
  ON r.from_prompt_uid=e.prompt_uid
 AND r.relation_type IN ('fix_prompt','resolved_by','replacement')
WHERE e.result='BLOCKED'
GROUP BY COALESCE(e.blocker_class, 'unclassified')
ORDER BY occurrences DESC, blocker_class;
