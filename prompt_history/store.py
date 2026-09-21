from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schema" / "prompt_evidence.sql"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def text_hash(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value or "").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def connect(path: str | Path) -> sqlite3.Connection:
    db_path = Path(path).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()


def prompt_uid_for(record: dict[str, Any]) -> str:
    explicit = record.get("prompt_uid")
    if explicit:
        return str(explicit)
    prompt_id = record.get("prompt_id")
    if prompt_id:
        return f"codex:{prompt_id}"
    source = str(record.get("source") or "unknown")
    source_key = str(record.get("source_key") or payload_hash(record)[:24])
    if source == "chatgpt":
        return f"chatgpt:{source_key}"
    if source.startswith("codex"):
        return f"codex-cycle:{source_key}"
    return f"{source}:{source_key}"


def _json_metadata(record: dict[str, Any], excluded: Iterable[str]) -> str:
    excluded_set = set(excluded)
    metadata = dict(record.get("metadata") or {})
    for key, value in record.items():
        if key not in excluded_set and key not in {"kind", "metadata"}:
            metadata.setdefault(key, value)
    return canonical_json(metadata)


def record_source(conn: sqlite3.Connection, record: dict[str, Any]) -> bool:
    source = str(record.get("source") or "unknown")
    source_key = str(record.get("source_key") or payload_hash(record)[:24])
    digest = payload_hash(record)
    cur = conn.execute(
        """INSERT OR IGNORE INTO source_records(source,source_key,payload_hash,kind,ingested_at)
           VALUES(?,?,?,?,?)""",
        (source, source_key, digest, str(record.get("kind") or "unknown"), utc_now()),
    )
    return cur.rowcount > 0


def _refresh_fts(conn: sqlite3.Connection, prompt_uid: str) -> None:
    row = conn.execute(
        "SELECT prompt_uid,title,prompt_text,repo,task_type FROM prompts WHERE prompt_uid=?",
        (prompt_uid,),
    ).fetchone()
    if not row:
        return
    conn.execute("DELETE FROM prompt_fts WHERE prompt_uid=?", (prompt_uid,))
    conn.execute(
        "INSERT INTO prompt_fts(prompt_uid,title,prompt_text,repo,task_type) VALUES(?,?,?,?,?)",
        (
            row["prompt_uid"],
            row["title"] or "",
            row["prompt_text"] or "",
            row["repo"] or "",
            row["task_type"] or "",
        ),
    )


def upsert_prompt(conn: sqlite3.Connection, record: dict[str, Any]) -> str:
    uid = prompt_uid_for(record)
    now = utc_now()
    prompt_text = str(record.get("prompt_text") or "")
    quality = int(record.get("text_quality") or 0)
    excluded = {
        "prompt_uid", "source", "source_key", "prompt_id", "conversation_id", "message_id",
        "parent_prompt_uid", "role", "title", "prompt_text", "prompt_hash", "text_quality",
        "project_id", "repo", "task_type", "created_at",
    }
    conn.execute(
        """INSERT INTO prompts(
             prompt_uid,source,source_key,prompt_id,conversation_id,message_id,parent_prompt_uid,
             role,title,prompt_text,prompt_hash,text_quality,project_id,repo,task_type,created_at,
             metadata_json,inserted_at,updated_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(prompt_uid) DO UPDATE SET
             source=CASE WHEN excluded.text_quality >= prompts.text_quality THEN excluded.source ELSE prompts.source END,
             source_key=CASE WHEN excluded.text_quality >= prompts.text_quality THEN excluded.source_key ELSE prompts.source_key END,
             prompt_id=COALESCE(excluded.prompt_id,prompts.prompt_id),
             conversation_id=COALESCE(excluded.conversation_id,prompts.conversation_id),
             message_id=COALESCE(excluded.message_id,prompts.message_id),
             parent_prompt_uid=COALESCE(excluded.parent_prompt_uid,prompts.parent_prompt_uid),
             role=COALESCE(excluded.role,prompts.role),
             title=COALESCE(excluded.title,prompts.title),
             prompt_text=CASE
               WHEN excluded.prompt_text<>'' AND excluded.text_quality>=prompts.text_quality
               THEN excluded.prompt_text ELSE prompts.prompt_text END,
             prompt_hash=CASE
               WHEN excluded.prompt_text<>'' AND excluded.text_quality>=prompts.text_quality
               THEN excluded.prompt_hash ELSE prompts.prompt_hash END,
             text_quality=MAX(prompts.text_quality,excluded.text_quality),
             project_id=COALESCE(excluded.project_id,prompts.project_id),
             repo=COALESCE(excluded.repo,prompts.repo),
             task_type=COALESCE(excluded.task_type,prompts.task_type),
             created_at=COALESCE(prompts.created_at,excluded.created_at),
             metadata_json=CASE WHEN excluded.text_quality>=prompts.text_quality
               THEN excluded.metadata_json ELSE prompts.metadata_json END,
             updated_at=excluded.updated_at""",
        (
            uid,
            str(record.get("source") or "unknown"),
            str(record.get("source_key") or uid),
            str(record["prompt_id"]) if record.get("prompt_id") is not None else None,
            str(record["conversation_id"]) if record.get("conversation_id") is not None else None,
            str(record["message_id"]) if record.get("message_id") is not None else None,
            record.get("parent_prompt_uid"),
            record.get("role"),
            record.get("title"),
            prompt_text,
            text_hash(prompt_text) if prompt_text else None,
            quality,
            str(record["project_id"]) if record.get("project_id") is not None else None,
            record.get("repo"),
            record.get("task_type"),
            record.get("created_at"),
            _json_metadata(record, excluded),
            now,
            now,
        ),
    )
    _refresh_fts(conn, uid)
    return uid


def ensure_prompt(
    conn: sqlite3.Connection,
    *,
    prompt_uid: str | None = None,
    prompt_id: str | None = None,
    source: str = "derived",
) -> str:
    if prompt_uid:
        row = conn.execute("SELECT prompt_uid FROM prompts WHERE prompt_uid=?", (prompt_uid,)).fetchone()
        if row:
            return str(row["prompt_uid"])
    if prompt_id:
        row = conn.execute("SELECT prompt_uid FROM prompts WHERE prompt_id=?", (str(prompt_id),)).fetchone()
        if row:
            return str(row["prompt_uid"])
    return upsert_prompt(
        conn,
        {
            "kind": "prompt",
            "source": source,
            "source_key": prompt_id or prompt_uid or "unknown",
            "prompt_uid": prompt_uid,
            "prompt_id": prompt_id,
            "prompt_text": "",
            "text_quality": 0,
        },
    )


def _acceptance(result: str | None) -> int | None:
    if not result or result.upper() in {"UNKNOWN", "RETRY", "CANCELLED"}:
        return None
    return 1 if result.upper() == "PASS" else 0


def upsert_execution(conn: sqlite3.Connection, record: dict[str, Any]) -> str:
    source = str(record.get("source") or "unknown")
    source_key = str(record.get("source_key") or payload_hash(record)[:24])
    uid = str(record.get("execution_uid") or f"{source}:{source_key}")
    prompt_uid = ensure_prompt(
        conn,
        prompt_uid=record.get("prompt_uid"),
        prompt_id=str(record["prompt_id"]) if record.get("prompt_id") is not None else None,
        source=source,
    )
    now = utc_now()
    result = str(record["result"]).upper() if record.get("result") else None
    excluded = {
        "execution_uid", "prompt_uid", "prompt_id", "source", "source_key", "session_id",
        "model", "variant", "reasoning", "duration_seconds", "input_tokens", "output_tokens",
        "cached_tokens", "total_tokens", "tool_calls", "result", "blocker_class",
        "acceptance_passed", "started_at", "ended_at",
    }
    conn.execute(
        """INSERT INTO executions(
             execution_uid,prompt_uid,source,source_key,session_id,model,variant,reasoning,
             duration_seconds,input_tokens,output_tokens,cached_tokens,total_tokens,tool_calls,
             result,blocker_class,acceptance_passed,started_at,ended_at,metadata_json,inserted_at,updated_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(execution_uid) DO UPDATE SET
             prompt_uid=excluded.prompt_uid,
             session_id=COALESCE(excluded.session_id,executions.session_id),
             model=COALESCE(excluded.model,executions.model),
             variant=COALESCE(excluded.variant,executions.variant),
             reasoning=COALESCE(excluded.reasoning,executions.reasoning),
             duration_seconds=COALESCE(excluded.duration_seconds,executions.duration_seconds),
             input_tokens=COALESCE(excluded.input_tokens,executions.input_tokens),
             output_tokens=COALESCE(excluded.output_tokens,executions.output_tokens),
             cached_tokens=COALESCE(excluded.cached_tokens,executions.cached_tokens),
             total_tokens=COALESCE(excluded.total_tokens,executions.total_tokens),
             tool_calls=COALESCE(excluded.tool_calls,executions.tool_calls),
             result=COALESCE(excluded.result,executions.result),
             blocker_class=COALESCE(excluded.blocker_class,executions.blocker_class),
             acceptance_passed=COALESCE(excluded.acceptance_passed,executions.acceptance_passed),
             started_at=COALESCE(executions.started_at,excluded.started_at),
             ended_at=COALESCE(excluded.ended_at,executions.ended_at),
             metadata_json=excluded.metadata_json,
             updated_at=excluded.updated_at""",
        (
            uid, prompt_uid, source, source_key, record.get("session_id"), record.get("model"),
            record.get("variant"), record.get("reasoning"), record.get("duration_seconds"),
            record.get("input_tokens"), record.get("output_tokens"), record.get("cached_tokens"),
            record.get("total_tokens"), record.get("tool_calls"), result, record.get("blocker_class"),
            record.get("acceptance_passed", _acceptance(result)), record.get("started_at"),
            record.get("ended_at"), _json_metadata(record, excluded), now, now,
        ),
    )
    return uid


def upsert_relation(conn: sqlite3.Connection, record: dict[str, Any]) -> str:
    source = str(record.get("source") or "derived")
    source_key = str(record.get("source_key") or payload_hash(record)[:24])
    from_uid = ensure_prompt(
        conn,
        prompt_uid=record.get("from_prompt_uid"),
        prompt_id=str(record["from_prompt_id"]) if record.get("from_prompt_id") is not None else None,
        source=source,
    )
    to_uid = ensure_prompt(
        conn,
        prompt_uid=record.get("to_prompt_uid"),
        prompt_id=str(record["to_prompt_id"]) if record.get("to_prompt_id") is not None else None,
        source=source,
    )
    relation_type = str(record.get("relation_type") or "related")
    uid = str(record.get("relation_uid") or f"{source}:{source_key}:{relation_type}")
    conn.execute(
        """INSERT INTO relations(
             relation_uid,source,source_key,from_prompt_uid,to_prompt_uid,relation_type,
             confidence,metadata_json,created_at
           ) VALUES(?,?,?,?,?,?,?,?,?)
           ON CONFLICT(relation_uid) DO UPDATE SET
             from_prompt_uid=excluded.from_prompt_uid,
             to_prompt_uid=excluded.to_prompt_uid,
             relation_type=excluded.relation_type,
             confidence=COALESCE(excluded.confidence,relations.confidence),
             metadata_json=excluded.metadata_json""",
        (
            uid, source, source_key, from_uid, to_uid, relation_type, record.get("confidence"),
            canonical_json(record.get("metadata") or {}), record.get("created_at") or utc_now(),
        ),
    )
    return uid


def upsert_artifact(conn: sqlite3.Connection, record: dict[str, Any]) -> str:
    source = str(record.get("source") or "unknown")
    source_key = str(record.get("source_key") or payload_hash(record)[:24])
    prompt_uid = ensure_prompt(
        conn,
        prompt_uid=record.get("prompt_uid"),
        prompt_id=str(record["prompt_id"]) if record.get("prompt_id") is not None else None,
        source=source,
    )
    artifact_type = str(record.get("artifact_type") or "unknown")
    artifact_key = str(record.get("artifact_key") or source_key)
    uid = str(record.get("artifact_uid") or f"{source}:{artifact_type}:{artifact_key}")
    conn.execute(
        """INSERT INTO artifacts(
             artifact_uid,prompt_uid,source,source_key,artifact_type,artifact_key,status,url,
             metadata_json,created_at,inserted_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(artifact_uid) DO UPDATE SET
             prompt_uid=excluded.prompt_uid,
             status=COALESCE(excluded.status,artifacts.status),
             url=COALESCE(excluded.url,artifacts.url),
             metadata_json=excluded.metadata_json""",
        (
            uid, prompt_uid, source, source_key, artifact_type, artifact_key, record.get("status"),
            record.get("url"), canonical_json(record.get("metadata") or {}),
            record.get("created_at"), utc_now(),
        ),
    )
    return uid


def ingest_record(conn: sqlite3.Connection, record: dict[str, Any]) -> bool:
    if not record_source(conn, record):
        return False
    kind = str(record.get("kind") or "").lower()
    if kind == "prompt":
        upsert_prompt(conn, record)
    elif kind == "execution":
        upsert_execution(conn, record)
    elif kind == "relation":
        upsert_relation(conn, record)
    elif kind == "artifact":
        upsert_artifact(conn, record)
    else:
        raise ValueError(f"unsupported record kind: {kind!r}")
    return True


def ingest_records(conn: sqlite3.Connection, records: Iterable[dict[str, Any]]) -> int:
    count = 0
    with conn:
        for record in records:
            count += int(ingest_record(conn, record))
    return count
