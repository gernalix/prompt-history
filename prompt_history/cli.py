from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .adapters import (
    ingest_chatgpt_export,
    ingest_codex_usage,
    ingest_jsonl,
    ingest_roadmap,
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
    clauses = ["e.model IS NOT NULL"]
    params: list[Any] = []
    if repo:
        clauses.append("p.repo=?")
        params.append(repo)
    if task_type:
        clauses.append("p.task_type=?")
        params.append(task_type)
    return conn.execute(
        f"""SELECT e.model,e.variant,e.reasoning,
                   COUNT(*) AS executions,
                   SUM(CASE WHEN e.result='PASS' THEN 1 ELSE 0 END) AS pass_count,
                   SUM(CASE WHEN e.result='BLOCKED' THEN 1 ELSE 0 END) AS blocked_count,
                   SUM(CASE WHEN e.result='FAIL' THEN 1 ELSE 0 END) AS fail_count,
                   ROUND(1.0*SUM(CASE WHEN e.result='PASS' THEN 1 ELSE 0 END)/COUNT(*),4) AS pass_rate,
                   ROUND(AVG(e.total_tokens),1) AS avg_total_tokens,
                   ROUND(AVG(e.tool_calls),1) AS avg_tool_calls,
                   ROUND(AVG(e.duration_seconds),1) AS avg_duration_seconds
            FROM executions e
            JOIN prompts p ON p.prompt_uid=e.prompt_uid
            WHERE {' AND '.join(clauses)}
            GROUP BY e.model,e.variant,e.reasoning
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

    p = sub.add_parser("blockers")
    p.add_argument("--db", required=True)
    p.set_defaults(func=cmd_blockers)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
