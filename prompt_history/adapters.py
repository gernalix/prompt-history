from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterator

from .store import ingest_record, ingest_records, prompt_uid_for, utc_now


def _variant(model: str | None) -> str | None:
    if not model:
        return None
    lowered = model.lower()
    for name in ("luna", "terra", "sol"):
        if name in lowered:
            return name.capitalize()
    return None


def _read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: invalid JSON: {exc}") from exc


def ingest_jsonl(conn: sqlite3.Connection, path: str | Path) -> int:
    return ingest_records(conn, _read_jsonl(Path(path).expanduser()))


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (table,),
    ).fetchone() is not None


def ingest_roadmap(conn: sqlite3.Connection, roadmap_path: str | Path) -> int:
    path = Path(roadmap_path).expanduser().resolve()
    src = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    count = 0
    try:
        materializations: dict[str, str] = {}
        if _table_exists(src, "prompt_materializations"):
            materializations = {
                str(row["prompt_id"]): str(row["body"])
                for row in src.execute("SELECT prompt_id,body FROM prompt_materializations")
            }

        with conn:
            for row in src.execute("SELECT * FROM prompts"):
                item = dict(row)
                prompt_id = str(item["prompt_id"])
                body = materializations.get(prompt_id) or ""
                if not body:
                    body = "\n\n".join(
                        x for x in [str(item.get("title") or ""), str(item.get("explanation") or "")]
                        if x
                    )
                record = {
                    "kind": "prompt",
                    "source": "codex-roadmap",
                    "source_key": prompt_id,
                    "prompt_id": prompt_id,
                    "title": item.get("title"),
                    "prompt_text": body,
                    "text_quality": 100 if prompt_id in materializations else 65,
                    "project_id": item.get("project_id"),
                    "repo": item.get("repo"),
                    "task_type": item.get("prompt_type"),
                    "created_at": item.get("created_at"),
                    "metadata": item,
                }
                count += int(ingest_record(conn, record))

            if _table_exists(src, "executions"):
                for row in src.execute("SELECT * FROM executions"):
                    item = dict(row)
                    record = {
                        "kind": "execution",
                        "source": "codex-roadmap",
                        "source_key": item.get("cycle_key") or f"execution-{item['execution_id']}",
                        "prompt_id": str(item["prompt_id"]),
                        "session_id": item.get("cycle_key"),
                        "model": item.get("model"),
                        "variant": _variant(item.get("model")),
                        "reasoning": item.get("reasoning"),
                        "duration_seconds": item.get("duration_seconds"),
                        "input_tokens": item.get("input_tokens"),
                        "output_tokens": item.get("output_tokens"),
                        "cached_tokens": item.get("cached_input_tokens"),
                        "total_tokens": item.get("total_tokens"),
                        "tool_calls": item.get("tool_call_count"),
                        "result": item.get("outcome"),
                        "started_at": item.get("started_at"),
                        "ended_at": item.get("ended_at"),
                        "metadata": item,
                    }
                    count += int(ingest_record(conn, record))

            if _table_exists(src, "prompt_relations"):
                for row in src.execute("SELECT * FROM prompt_relations"):
                    item = dict(row)
                    record = {
                        "kind": "relation",
                        "source": "codex-roadmap",
                        "source_key": f"{item['from_prompt_id']}:{item['to_prompt_id']}:{item['relation_type']}",
                        "from_prompt_id": str(item["from_prompt_id"]),
                        "to_prompt_id": str(item["to_prompt_id"]),
                        "relation_type": str(item["relation_type"]),
                        "created_at": item.get("created_at"),
                        "metadata": {"actor": item.get("actor"), "note": item.get("note")},
                    }
                    count += int(ingest_record(conn, record))

            if _table_exists(src, "dependencies"):
                for row in src.execute("SELECT * FROM dependencies"):
                    item = dict(row)
                    record = {
                        "kind": "relation",
                        "source": "codex-roadmap",
                        "source_key": f"dependency:{item['prompt_id']}:{item['depends_on_prompt_id']}",
                        "from_prompt_id": str(item["prompt_id"]),
                        "to_prompt_id": str(item["depends_on_prompt_id"]),
                        "relation_type": "depends_on",
                        "metadata": {"note": item.get("note")},
                    }
                    count += int(ingest_record(conn, record))

            if _table_exists(src, "analyses"):
                for row in src.execute(
                    "SELECT * FROM analyses WHERE fix_prompt_id IS NOT NULL"
                ):
                    item = dict(row)
                    record = {
                        "kind": "relation",
                        "source": "codex-roadmap",
                        "source_key": f"analysis-fix:{item['analysis_id']}",
                        "from_prompt_id": str(item["prompt_id"]),
                        "to_prompt_id": str(item["fix_prompt_id"]),
                        "relation_type": "fix_prompt",
                        "created_at": item.get("analyzed_at"),
                        "metadata": {
                            "summary": item.get("summary"),
                            "source_ref": item.get("source_ref"),
                            "bottlenecks_found": item.get("bottlenecks_found"),
                        },
                    }
                    count += int(ingest_record(conn, record))

            if _table_exists(src, "artifacts"):
                for row in src.execute("SELECT * FROM artifacts"):
                    item = dict(row)
                    record = {
                        "kind": "artifact",
                        "source": "codex-roadmap",
                        "source_key": f"artifact-{item['artifact_id']}",
                        "prompt_id": str(item["prompt_id"]),
                        "artifact_type": str(item["artifact_type"]),
                        "artifact_key": str(item["uri"]),
                        "url": item.get("uri"),
                        "created_at": item.get("created_at"),
                        "metadata": {"label": item.get("label")},
                    }
                    count += int(ingest_record(conn, record))
    finally:
        src.close()
    return count


def _content_text(content: Any) -> str:
    if not isinstance(content, dict):
        return ""
    parts = content.get("parts")
    if isinstance(parts, list):
        out: list[str] = []
        for part in parts:
            if isinstance(part, str):
                out.append(part)
            elif isinstance(part, dict):
                if isinstance(part.get("text"), str):
                    out.append(part["text"])
                elif isinstance(part.get("content"), str):
                    out.append(part["content"])
        return "\n".join(x for x in out if x).strip()
    for key in ("text", "content"):
        if isinstance(content.get(key), str):
            return content[key].strip()
    return ""


def _iso_from_epoch(value: Any) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    from datetime import datetime, timezone
    return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def ingest_chatgpt_export(conn: sqlite3.Connection, conversations_path: str | Path) -> int:
    path = Path(conversations_path).expanduser()
    conversations = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(conversations, list):
        raise ValueError("ChatGPT conversations export must be a JSON list")

    count = 0
    with conn:
        for index, conversation in enumerate(conversations):
            if not isinstance(conversation, dict):
                continue
            conversation_id = str(
                conversation.get("id")
                or conversation.get("conversation_id")
                or f"conversation-{index}"
            )
            title = conversation.get("title")
            mapping = conversation.get("mapping") or {}
            if not isinstance(mapping, dict):
                continue

            uid_by_node: dict[str, str] = {}
            for node_id, node in mapping.items():
                if not isinstance(node, dict):
                    continue
                message = node.get("message")
                if not isinstance(message, dict):
                    continue
                author = message.get("author") or {}
                role = author.get("role") if isinstance(author, dict) else None
                if role not in {"user", "assistant"}:
                    continue
                message_id = str(message.get("id") or node_id)
                source_key = f"{conversation_id}:{message_id}"
                metadata = dict(message.get("metadata") or {})
                record = {
                    "kind": "prompt",
                    "source": "chatgpt",
                    "source_key": source_key,
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                    "role": role,
                    "title": title,
                    "prompt_text": _content_text(message.get("content")),
                    "text_quality": 100,
                    "created_at": _iso_from_epoch(message.get("create_time")),
                    "metadata": {
                        "conversation_title": title,
                        "model_slug": metadata.get("model_slug"),
                        "recipient": message.get("recipient"),
                    },
                }
                uid_by_node[str(node_id)] = prompt_uid_for(record)
                count += int(ingest_record(conn, record))

            for node_id, node in mapping.items():
                if str(node_id) not in uid_by_node or not isinstance(node, dict):
                    continue
                parent = node.get("parent")
                if parent is None or str(parent) not in uid_by_node:
                    continue
                relation = {
                    "kind": "relation",
                    "source": "chatgpt",
                    "source_key": f"{conversation_id}:{parent}->{node_id}",
                    "from_prompt_uid": uid_by_node[str(parent)],
                    "to_prompt_uid": uid_by_node[str(node_id)],
                    "relation_type": "conversation_parent",
                    "confidence": 1.0,
                }
                count += int(ingest_record(conn, relation))
    return count


def ingest_codex_usage(conn: sqlite3.Connection, repo_path: str | Path) -> int:
    root = Path(repo_path).expanduser()
    index_path = root / "index" / "prompts.jsonl"
    if not index_path.is_file():
        raise FileNotFoundError(index_path)

    count = 0
    with conn:
        for index_record in _read_jsonl(index_path):
            rel_path = index_record.get("path")
            if not rel_path:
                continue
            metrics_path = root / str(rel_path) / "metrics.json"
            metrics: dict[str, Any] = {}
            if metrics_path.is_file():
                metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            merged = {**index_record, **metrics}
            prompt_id = merged.get("prompt_id")
            cycle_key = str(merged.get("cycle_key") or Path(str(rel_path)).name)
            source_key = str(prompt_id) if prompt_id else cycle_key
            prompt_record = {
                "kind": "prompt",
                "source": "codex-usage",
                "source_key": source_key,
                "prompt_id": str(prompt_id) if prompt_id is not None else None,
                "conversation_id": str(merged["chat_id"]) if merged.get("chat_id") is not None else None,
                "prompt_text": str(merged.get("prompt_text_redacted") or ""),
                "text_quality": 40,
                "created_at": merged.get("timestamp_start_utc"),
                "metadata": {
                    "native_session_id": merged.get("native_session_id"),
                    "repo_project": merged.get("repo_project"),
                    "repo_projects": merged.get("repo_projects"),
                    "repo_paths": merged.get("repo_paths"),
                    "final_response_redacted": merged.get("final_response_redacted"),
                },
            }
            count += int(ingest_record(conn, prompt_record))
            execution_record = {
                "kind": "execution",
                "source": "codex-usage",
                "source_key": cycle_key,
                "prompt_id": str(prompt_id) if prompt_id is not None else None,
                "prompt_uid": None if prompt_id is not None else f"codex-cycle:{source_key}",
                "session_id": merged.get("native_session_id"),
                "model": merged.get("model"),
                "variant": _variant(merged.get("model")),
                "reasoning": merged.get("reasoning_effort"),
                "duration_seconds": merged.get("duration_seconds"),
                "input_tokens": merged.get("input_tokens"),
                "output_tokens": merged.get("output_tokens"),
                "cached_tokens": merged.get("cached_input_tokens"),
                "total_tokens": merged.get("total_tokens"),
                "tool_calls": merged.get("tool_call_count"),
                "result": merged.get("status"),
                "started_at": merged.get("timestamp_start_utc"),
                "ended_at": merged.get("timestamp_end_utc"),
                "metadata": {
                    "quota_delta_observed": merged.get("quota_delta_observed"),
                    "quota_weekly_before": merged.get("quota_weekly_before"),
                    "quota_weekly_after": merged.get("quota_weekly_after"),
                    "reasoning_output_tokens": merged.get("reasoning_output_tokens"),
                    "uncached_input_tokens": merged.get("uncached_input_tokens"),
                    "tool_calls_by_type": merged.get("tool_calls_by_type"),
                    "turn_count": merged.get("turn_count"),
                },
            }
            count += int(ingest_record(conn, execution_record))
    return count
