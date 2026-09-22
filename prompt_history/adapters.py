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


def _blocker_class(status: str | None, final_response: str | None) -> str | None:
    if str(status or "").upper() != "BLOCKED" or not final_response:
        return None
    match = re.search(r"(?im)^BLOCKER\s*=\s*(.+)$", final_response)
    if not match:
        return "unclassified"
    blocker = match.group(1).strip().lower()
    if blocker in {"", "none", "n/a"}:
        return "unclassified"
    if "claim" in blocker or "not_planned" in blocker:
        return "roadmap_claim"
    if "rate limit" in blocker or "429" in blocker:
        return "rate_limit"
    if any(term in blocker for term in ("credential", "password", "login", "2fa", "auth")):
        return "authentication"
    if any(term in blocker for term in ("manual", "confirm", "click", "load ") ):
        return "manual_action_required"
    if "heartbeat" in blocker:
        return "heartbeat"
    if any(term in blocker for term in ("test", "ci", "workflow")):
        return "test_or_ci"
    return "other"


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

            if _table_exists(src, "analyses") and _table_exists(src, "executions"):
                for row in src.execute(
                    """SELECT a.analysis_id,a.prompt_id,a.fix_prompt_id,a.analyzed_at
                       FROM analyses a
                       WHERE a.fix_prompt_id IS NOT NULL
                         AND EXISTS(
                           SELECT 1 FROM executions e
                           WHERE e.prompt_id=a.fix_prompt_id AND e.outcome='PASS'
                         )"""
                ):
                    item = dict(row)
                    record = {
                        "kind": "relation",
                        "source": "codex-roadmap",
                        "source_key": f"analysis-resolved:{item['analysis_id']}",
                        "from_prompt_id": str(item["prompt_id"]),
                        "to_prompt_id": str(item["fix_prompt_id"]),
                        "relation_type": "resolved_by",
                        "confidence": 1.0,
                        "created_at": item.get("analyzed_at"),
                        "metadata": {"derived_from": "analysis.fix_prompt_id + PASS execution"},
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
    count += link_explicit_prompt_ids(conn)
    return count



def _normalized_parts_text(parts: Any) -> str:
    if not isinstance(parts, list):
        return ""
    out: list[str] = []
    for part in parts:
        if isinstance(part, str):
            if part:
                out.append(part)
            continue
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str) and text:
            out.append(text)
    return "\n".join(out).strip()


def ingest_chatgpt_exporter_archive(conn: sqlite3.Connection, archive_path: str | Path) -> int:
    """Ingest ChatGPTExporter normalized conversation.json files read-only.

    ChatGPTExporter is an MIT upstream that already owns live Web capture. This
    adapter deliberately consumes its stable normalized archive instead of
    reimplementing private ChatGPT Web endpoints here.
    """
    root = Path(archive_path).expanduser()
    conversation_root = root / "conversations"
    if not conversation_root.is_dir():
        raise FileNotFoundError(conversation_root)

    paths = sorted(conversation_root.glob("*/conversation.json"))
    if not paths:
        raise ValueError(f"no ChatGPTExporter conversation.json files under {conversation_root}")

    count = 0
    for path in paths:
        conversation = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(conversation, dict):
            continue
        if conversation.get("provider") not in {None, "chatgpt-web"}:
            continue
        conversation_id = str(conversation.get("conversationId") or path.parent.name)
        title = conversation.get("title")
        workspace = conversation.get("workspaceFingerprint")
        messages = conversation.get("messages") or []
        memberships = conversation.get("memberships") or []
        if not isinstance(messages, list):
            continue

        uid_by_node: dict[str, str] = {}
        pending_relations: list[tuple[str, str]] = []
        with conn:
            for index, message in enumerate(messages):
                if not isinstance(message, dict):
                    continue
                role = message.get("role")
                if role not in {"user", "assistant"}:
                    continue
                message_id = str(message.get("id") or f"message-{index}")
                node_id = str(message.get("nodeId") or message_id)
                source_key = f"{conversation_id}:{message_id}"
                record = {
                    "kind": "prompt",
                    "source": "chatgpt",
                    "source_key": source_key,
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                    "role": role,
                    "title": title,
                    "prompt_text": _normalized_parts_text(message.get("parts")),
                    "text_quality": 100,
                    "created_at": _iso_from_epoch(message.get("createTime")),
                    "metadata": {
                        "provider": "chatgpt-web",
                        "upstream": "siraht/ChatGPTExporter",
                        "normalizer_version": conversation.get("normalizerVersion"),
                        "workspace_fingerprint": workspace,
                        "memberships": memberships,
                        "model_slug": message.get("modelSlug"),
                        "recipient": message.get("recipient"),
                        "selected": message.get("selected"),
                        "status": message.get("status"),
                    },
                }
                uid = prompt_uid_for(record)
                uid_by_node[node_id] = uid
                count += int(ingest_record(conn, record))
                parent_id = message.get("parentId")
                if parent_id is not None:
                    pending_relations.append((str(parent_id), node_id))

            for parent_node, child_node in pending_relations:
                if parent_node not in uid_by_node or child_node not in uid_by_node:
                    continue
                relation = {
                    "kind": "relation",
                    "source": "chatgpt",
                    "source_key": f"{conversation_id}:{parent_node}->{child_node}",
                    "from_prompt_uid": uid_by_node[parent_node],
                    "to_prompt_uid": uid_by_node[child_node],
                    "relation_type": "conversation_parent",
                    "confidence": 1.0,
                    "metadata": {"upstream": "siraht/ChatGPTExporter"},
                }
                count += int(ingest_record(conn, relation))
    count += link_explicit_prompt_ids(conn)
    return count


def _json_records(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if text.startswith("["):
        value = json.loads(text)
        if not isinstance(value, list):
            raise ValueError("expected JSON array")
        return [row for row in value if isinstance(row, dict)]
    rows: list[dict[str, Any]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{lineno}: expected JSON object")
        rows.append(value)
    return rows


def ingest_session_bandit_export(conn: sqlite3.Connection, input_path: str | Path) -> int:
    """Ingest normalized Codex sessions emitted by @session-bandit/core.

    The companion tools/session_bandit_dump.mjs bridge delegates Codex format
    handling to Session Bandit's MIT adapter, including its legacy and modern
    rollout formats. codex-usage remains the canonical execution-metrics source;
    this adapter contributes transcript/provenance without double-counting cost.
    """
    path = Path(input_path).expanduser()
    sessions = _json_records(path)
    count = 0

    with conn:
        for session in sessions:
            if session.get("agent") != "codex":
                continue
            session_id = str(session.get("sessionId") or "")
            if not session_id:
                continue
            messages = session.get("messages") or []
            if not isinstance(messages, list):
                continue
            previous_uid: str | None = None
            for index, message in enumerate(messages):
                if not isinstance(message, dict):
                    continue
                role = str(message.get("role") or "")
                if role not in {"user", "assistant", "system", "tool", "summary"}:
                    continue
                prompt_text = str(message.get("text") or "")
                prompt_id = None
                if role == "user":
                    match = re.search(r"(?<!PARENT_)PROMPT_ID\s*=\s*(\d{6})", prompt_text)
                    if match:
                        prompt_id = match.group(1)
                source_key = f"{session_id}:{index}"
                record = {
                    "kind": "prompt",
                    "source": "codex-session-bandit",
                    "source_key": source_key,
                    "prompt_id": prompt_id,
                    "conversation_id": session_id,
                    "message_id": str(index),
                    "role": role,
                    "title": session.get("project") or f"Codex {session_id[:12]}",
                    "prompt_text": prompt_text,
                    "text_quality": 90,
                    "created_at": message.get("timestamp") or session.get("startedAt"),
                    "metadata": {
                        "upstream": "janole/session-bandit",
                        "agent": "codex",
                        "file_path": session.get("filePath"),
                        "project": session.get("project"),
                        "cwd": session.get("cwd"),
                        "model": session.get("model"),
                        "ended_at": session.get("endedAt"),
                        "subtype": message.get("subtype"),
                        "tool_calls": message.get("toolCalls") or [],
                        "message_stats": message.get("stats"),
                        "session_stats": session.get("stats"),
                    },
                }
                uid = prompt_uid_for(record)
                count += int(ingest_record(conn, record))
                if previous_uid is not None:
                    count += int(ingest_record(conn, {
                        "kind": "relation",
                        "source": "codex-session-bandit",
                        "source_key": f"{session_id}:{index - 1}->{index}",
                        "from_prompt_uid": previous_uid,
                        "to_prompt_uid": uid,
                        "relation_type": "conversation_parent",
                        "confidence": 1.0,
                        "metadata": {"upstream": "janole/session-bandit"},
                    }))
                previous_uid = uid
    return count

def link_explicit_prompt_ids(conn: sqlite3.Connection) -> int:
    """Create only deterministic links backed by literal PROMPT_ID markers."""
    count = 0
    rows = conn.execute(
        """SELECT prompt_uid,role,prompt_text,source_key
           FROM prompts
           WHERE source='chatgpt' AND prompt_text<>''"""
    ).fetchall()
    with conn:
        for row in rows:
            text = str(row["prompt_text"] or "")
            ids = list(dict.fromkeys(re.findall(r"(?<!PARENT_)PROMPT_ID\s*=\s*(\d{6})", text)))
            relation_type = "generated" if row["role"] == "assistant" else "references_prompt"
            for prompt_id in ids:
                record = {
                    "kind": "relation",
                    "source": "chatgpt-linker",
                    "source_key": f"{row['source_key']}:{relation_type}:{prompt_id}",
                    "from_prompt_uid": str(row["prompt_uid"]),
                    "to_prompt_id": prompt_id,
                    "relation_type": relation_type,
                    "confidence": 1.0,
                    "metadata": {"evidence": "literal PROMPT_ID marker"},
                }
                count += int(ingest_record(conn, record))

            child = re.search(r"(?<!PARENT_)PROMPT_ID\s*=\s*(\d{6})", text)
            parent = re.search(r"PARENT_PROMPT_ID\s*=\s*(\d{6})", text)
            if child and parent and child.group(1) != parent.group(1):
                record = {
                    "kind": "relation",
                    "source": "chatgpt-linker",
                    "source_key": f"{row['source_key']}:parent:{parent.group(1)}:{child.group(1)}",
                    "from_prompt_id": parent.group(1),
                    "to_prompt_id": child.group(1),
                    "relation_type": "parent",
                    "confidence": 1.0,
                    "metadata": {"evidence": "literal PARENT_PROMPT_ID + PROMPT_ID markers"},
                }
                count += int(ingest_record(conn, record))
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
                "blocker_class": _blocker_class(
                    merged.get("status"), merged.get("final_response_redacted")
                ),
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
                    "final_response_redacted": merged.get("final_response_redacted"),
                },
            }
            count += int(ingest_record(conn, execution_record))
    return count


def ingest_switcher(conn: sqlite3.Connection, db_path: str | Path) -> int:
    """Read stable prompt bindings and browser contexts without mutating switcher state."""
    src = sqlite3.connect(f"file:{Path(db_path).expanduser().resolve()}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    count = 0
    try:
        contexts = {r["id"]: dict(r) for r in src.execute("SELECT * FROM contexts")}
        with conn:
            for row in src.execute("SELECT * FROM prompt_bindings"):
                item = dict(row)
                pid = str(item["prompt_id"])
                context = contexts.get(item.get("context_id"), {})
                record = {"kind": "prompt", "source": "chrome-codex-switcher",
                          "source_key": pid, "prompt_id": pid, "prompt_text": "",
                          "text_quality": 0, "conversation_id": item.get("context_id"),
                          "metadata": {"codex_thread": item.get("codex_thread"),
                                       "codex_deep_link": item.get("codex_deep_link"),
                                       "context_url": context.get("url"),
                                       "context_title": context.get("title"),
                                       "note": context.get("note")}}
                count += int(ingest_record(conn, record))
                if item.get("codex_thread") or item.get("codex_deep_link"):
                    artifact = {"kind": "artifact", "source": "chrome-codex-switcher",
                                "source_key": pid + ":binding", "prompt_id": pid,
                                "artifact_type": "codex_binding",
                                "artifact_key": item.get("codex_deep_link") or item.get("codex_thread"),
                                "url": item.get("codex_deep_link"),
                                "metadata": {"context_id": item.get("context_id")}}
                    count += int(ingest_record(conn, artifact))
    finally:
        src.close()
    return count
