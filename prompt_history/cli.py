from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .adapters import (
    ingest_chatgpt_export,
    ingest_chatgpt_exporter_archive,
    ingest_codex_usage,
    ingest_jsonl,
    ingest_roadmap,
    ingest_session_bandit_export,
    ingest_switcher,
    link_explicit_prompt_ids,
)
from .store import connect, init_db


def _print_rows(rows: list[sqlite3.Row]) -> None:
    for row in rows:
        print(json.dumps(dict(row), ensure_ascii=False, sort_keys=True))


def _fts_query(text: str) -> str:
    tokens = re.findall(r"[\w-]{3,}", text, flags=re.UNICODE)
    if not tokens:
        raise ValueError("search text has no usable terms")
    return " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens[:24])


def cmd_init(args: argparse.Namespace) -> None:
    conn = connect(args.db)
    try:
        init_db(conn)
    finally:
        conn.close()
    print(f"initialized={Path(args.db).expanduser()}")


def _with_db(args: argparse.Namespace, fn: Any, *values: Any) -> None:
    conn = connect(args.db)
    try:
        init_db(conn)
        count = fn(conn, *values)
        conn.commit()
    finally:
        conn.close()
    print(f"ingested={count}")


def cmd_similar(args: argparse.Namespace) -> None:
    conn = connect(args.db)
    try:
        clauses = ["prompt_fts MATCH ?"]
        params: list[Any] = [_fts_query(args.text)]
        if args.repo:
            clauses.append("p.repo=?")
            params.append(args.repo)
        if args.task_type:
            clauses.append("p.task_type=?")
            params.append(args.task_type)
        params.append(args.limit)
        rows = conn.execute(
            f"""SELECT p.prompt_uid,p.prompt_id,p.source,p.title,p.repo,p.task_type,p.created_at,
                       snippet(prompt_fts,2,'[',']',' … ',24) AS snippet,
                       bm25(prompt_fts,2.0,5.0,1.5,1.5) AS lexical_rank
                FROM prompt_fts
                JOIN prompts p ON p.prompt_uid=prompt_fts.prompt_uid
                WHERE {' AND '.join(clauses)}
                ORDER BY lexical_rank
                LIMIT ?""",
            params,
        ).fetchall()
        _print_rows(rows)
    finally:
        conn.close()


def _model_rows(conn: sqlite3.Connection, repo: str | None, task_type: str | None) -> list[sqlite3.Row]:
    clauses = ["sample.model IS NOT NULL"]
    params: list[Any] = []
    if repo:
        clauses.append("p.repo=?")
        params.append(repo)
    if task_type:
        clauses.append("p.task_type=?")
        params.append(task_type)
    return conn.execute(
        f"""WITH codex AS (
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
                     CASE
                       WHEN input_tokens IS NOT NULL OR output_tokens IS NOT NULL
                       THEN COALESCE(input_tokens,0)+COALESCE(output_tokens,0)
                       ELSE 0
                     END
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
                   CASE WHEN x.detailed_cycles>0
                        THEN x.detailed_tokens
                        ELSE x.fallback_total_tokens
                   END AS total_tokens,
                   x.tool_calls,
                   x.duration_seconds,
                   r.result
                 FROM per_prompt x
                 JOIN effective_result r USING(prompt_uid)
                 WHERE x.model_count=1 AND x.reasoning_count<=1
               )
            SELECT sample.model,sample.variant,sample.reasoning,
                   COUNT(*) AS executions,
                   COUNT(*) AS prompts,
                   SUM(CASE WHEN sample.result='PASS' THEN 1 ELSE 0 END) AS pass_count,
                   SUM(CASE WHEN sample.result='BLOCKED' THEN 1 ELSE 0 END) AS blocked_count,
                   SUM(CASE WHEN sample.result='FAIL' THEN 1 ELSE 0 END) AS fail_count,
                   ROUND(1.0*SUM(CASE WHEN sample.result='PASS' THEN 1 ELSE 0 END)/COUNT(*),4) AS pass_rate,
                   ROUND(AVG(sample.total_tokens),1) AS avg_total_tokens,
                   ROUND(AVG(sample.tool_calls),1) AS avg_tool_calls,
                   ROUND(AVG(sample.duration_seconds),1) AS avg_duration_seconds
            FROM sample
            JOIN prompts p ON p.prompt_uid=sample.prompt_uid
            WHERE {' AND '.join(clauses)}
            GROUP BY sample.model,sample.variant,sample.reasoning
            ORDER BY pass_rate DESC, avg_total_tokens ASC, avg_tool_calls ASC""",
        params,
    ).fetchall()


def cmd_model_stats(args: argparse.Namespace) -> None:
    conn = connect(args.db)
    try:
        _print_rows(_model_rows(conn, args.repo, args.task_type))
    finally:
        conn.close()


def cmd_recommend(args: argparse.Namespace) -> None:
    conn = connect(args.db)
    try:
        rows = _model_rows(conn, args.repo, args.task_type)
        eligible = [row for row in rows if int(row["executions"]) >= args.min_samples]
        if not eligible:
            print(json.dumps({
                "recommended": None,
                "reason": "insufficient_history",
                "min_samples": args.min_samples,
                "candidates": [dict(row) for row in rows],
            }, ensure_ascii=False, sort_keys=True))
            return
        best = dict(eligible[0])
        print(json.dumps({
            "recommended": {
                "model": best["model"],
                "variant": best["variant"],
                "reasoning": best["reasoning"],
            },
            "evidence": best,
            "min_samples": args.min_samples,
            "candidate_count": len(eligible),
        }, ensure_ascii=False, sort_keys=True))
    finally:
        conn.close()


def cmd_link(args: argparse.Namespace) -> None:
    conn = connect(args.db)
    try:
        init_db(conn)
        count = link_explicit_prompt_ids(conn)
        conn.commit()
    finally:
        conn.close()
    print(f"linked={count}")


def cmd_sync(args: argparse.Namespace) -> None:
    """Run all configured local read-only importers; safe to repeat."""
    conn = connect(args.db)
    counts = {}
    try:
        init_db(conn)
        counts["roadmap"] = ingest_roadmap(conn, args.roadmap)
        counts["codex_usage"] = ingest_codex_usage(conn, args.codex_usage)
        if args.chatgpt and Path(args.chatgpt).is_file():
            counts["chatgpt"] = ingest_chatgpt_export(conn, args.chatgpt)
        else:
            counts["chatgpt"] = 0
        if args.chatgpt_exporter and Path(args.chatgpt_exporter).is_dir():
            counts["chatgpt_exporter"] = ingest_chatgpt_exporter_archive(conn, args.chatgpt_exporter)
        else:
            counts["chatgpt_exporter"] = 0
        if args.session_bandit and Path(args.session_bandit).is_file():
            counts["session_bandit"] = ingest_session_bandit_export(conn, args.session_bandit)
        else:
            counts["session_bandit"] = 0
        if args.switcher and Path(args.switcher).is_file():
            counts["switcher"] = ingest_switcher(conn, args.switcher)
        else:
            counts["switcher"] = 0
        counts["links"] = link_explicit_prompt_ids(conn)
        conn.commit()
    finally:
        conn.close()
    print(json.dumps(counts, sort_keys=True))


def cmd_blockers(args: argparse.Namespace) -> None:
    conn = connect(args.db)
    try:
        rows = conn.execute(
            """SELECT blocker_class,occurrences,prompts,first_seen_at,last_seen_at
               FROM v_blockers ORDER BY occurrences DESC, blocker_class"""
        ).fetchall()
        _print_rows(rows)
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="prompt-history")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init")
    p.add_argument("--db", required=True)
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("ingest-roadmap")
    p.add_argument("--db", required=True)
    p.add_argument("--roadmap", required=True)
    p.set_defaults(func=lambda a: _with_db(a, ingest_roadmap, a.roadmap))

    p = sub.add_parser("ingest-codex-usage")
    p.add_argument("--db", required=True)
    p.add_argument("--repo", required=True)
    p.set_defaults(func=lambda a: _with_db(a, ingest_codex_usage, a.repo))

    p = sub.add_parser("ingest-chatgpt")
    p.add_argument("--db", required=True)
    p.add_argument("--conversations", required=True)
    p.set_defaults(func=lambda a: _with_db(a, ingest_chatgpt_export, a.conversations))

    p = sub.add_parser("ingest-chatgpt-exporter")
    p.add_argument("--db", required=True)
    p.add_argument("--archive", required=True)
    p.set_defaults(func=lambda a: _with_db(a, ingest_chatgpt_exporter_archive, a.archive))

    p = sub.add_parser("ingest-session-bandit")
    p.add_argument("--db", required=True)
    p.add_argument("--input", required=True)
    p.set_defaults(func=lambda a: _with_db(a, ingest_session_bandit_export, a.input))

    p = sub.add_parser("ingest-jsonl")
    p.add_argument("--db", required=True)
    p.add_argument("--input", required=True)
    p.set_defaults(func=lambda a: _with_db(a, ingest_jsonl, a.input))

    p = sub.add_parser("similar")
    p.add_argument("--db", required=True)
    p.add_argument("--text", required=True)
    p.add_argument("--repo")
    p.add_argument("--task-type")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_similar)

    p = sub.add_parser("model-stats")
    p.add_argument("--db", required=True)
    p.add_argument("--repo")
    p.add_argument("--task-type")
    p.set_defaults(func=cmd_model_stats)

    p = sub.add_parser("recommend")
    p.add_argument("--db", required=True)
    p.add_argument("--repo")
    p.add_argument("--task-type")
    p.add_argument("--min-samples", type=int, default=3)
    p.set_defaults(func=cmd_recommend)

    p = sub.add_parser("link")
    p.add_argument("--db", required=True)
    p.set_defaults(func=cmd_link)

    p = sub.add_parser("sync")
    p.add_argument("--db", required=True)
    p.add_argument("--roadmap", required=True)
    p.add_argument("--codex-usage", required=True)
    p.add_argument("--chatgpt")
    p.add_argument("--chatgpt-exporter")
    p.add_argument("--session-bandit")
    p.add_argument("--switcher")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("blockers")
    p.add_argument("--db", required=True)
    p.set_defaults(func=cmd_blockers)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
