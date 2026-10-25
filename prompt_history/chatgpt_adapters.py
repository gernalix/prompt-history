"""Read-only ChatGPT Web archive ingestion and explicit prompt relation linking.

Both native conversation exports and ChatGPTExporter normalize through the
existing canonical evidence-store contract, without owning capture or runtime.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .store import ingest_record, prompt_uid_for, utc_now
from .incremental import atomic, checkpointed, get, put, stamp, transactional as _transactional


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
    try:
        seconds = float(value)
        if abs(seconds) >= 100_000_000_000:
            seconds /= 1000.0
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError):
        return None

@checkpointed(sqlite=False)
def ingest_chatgpt_export(conn: sqlite3.Connection, conversations_path: str | Path) -> int:
    path = Path(conversations_path).expanduser()
    conversations = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(conversations, list):
        raise ValueError("ChatGPT conversations export must be a JSON list")

    count = 0
    with atomic(conn):
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

CHATGPT_EXPORTER_INGEST_VERSION = 2

CHATGPT_EXPORTER_ARCHIVE_MARKER_VERSION = 1

def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def _chatgpt_exporter_file_seen(
    conn: sqlite3.Connection,
    *,
    source_key: str,
    digest: str,
) -> bool:
    row = conn.execute(
        """SELECT payload_hash FROM source_records
           WHERE source='chatgpt-exporter-file' AND source_key=?
           ORDER BY rowid DESC LIMIT 1""", (source_key,),
    ).fetchone()
    return row is not None and row[0] == digest

def _record_chatgpt_exporter_file(
    conn: sqlite3.Connection,
    *,
    source_key: str,
    digest: str,
) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO source_records(source,source_key,payload_hash,kind,ingested_at)
           VALUES('chatgpt-exporter-file',?,?, 'file', ?)""",
        (source_key, digest, utc_now()),
    )

def _chatgpt_exporter_archive_seen(
    conn: sqlite3.Connection,
    *,
    source_key: str,
    digest: str,
) -> bool:
    return conn.execute(
        """SELECT 1 FROM source_records
           WHERE source='chatgpt-exporter-archive' AND source_key=? AND payload_hash=?
           LIMIT 1""",
        (source_key, digest),
    ).fetchone() is not None

def _record_chatgpt_exporter_archive(
    conn: sqlite3.Connection,
    *,
    source_key: str,
    digest: str,
) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO source_records(source,source_key,payload_hash,kind,ingested_at)
           VALUES('chatgpt-exporter-archive',?,?, 'archive', ?)""",
        (source_key, digest, utc_now()),
    )

@_transactional
def ingest_chatgpt_exporter_archive(conn: sqlite3.Connection, archive_path: str | Path) -> int:
    """Ingest ChatGPTExporter normalized conversation.json files read-only.

    ChatGPTExporter is an MIT upstream that already owns live Web capture. This
    adapter deliberately consumes its stable normalized archive instead of
    reimplementing private ChatGPT Web endpoints here.
    """
    root = Path(archive_path).expanduser().resolve()
    if (root / "conversations").is_dir():
        archive_roots = [root]
    elif root.is_dir():
        archive_roots = sorted(
            candidate
            for candidate in root.glob("ChatGPTExport-*")
            if (candidate / "conversations").is_dir()
        )
    else:
        archive_roots = []
    if not archive_roots:
        raise FileNotFoundError(root / "conversations")

    paths = sorted(
        path for archive_root in archive_roots
        for path in (archive_root / "conversations").glob("*/conversation.json")
    )
    if not paths:
        raise ValueError(f"no ChatGPTExporter conversation.json files under {root}")

    count = 0
    for path in paths:
        marker_key = f"v{CHATGPT_EXPORTER_INGEST_VERSION}:{path.relative_to(root).as_posix()}"
        checkpoint_key = f"v3:exporter:{path}"
        before = stamp(path)
        if get(conn, checkpoint_key) == before:
            continue
        marker_digest = _file_sha256(path)
        # Upgrade a historical per-file checkpoint only after verifying its
        # current bytes. Never use an archive-wide manifest as that evidence.
        if get(conn, checkpoint_key) is None and _chatgpt_exporter_file_seen(
                conn, source_key=marker_key, digest=marker_digest):
            if stamp(path) != before:
                raise RuntimeError(f"input changed during ingestion: {path}")
            put(conn, checkpoint_key, before)
            continue
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
        with atomic(conn):
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

            if stamp(path) != before:
                raise RuntimeError(f"input changed during ingestion: {path}")
            put(conn, checkpoint_key, before)
            _record_chatgpt_exporter_file(
                conn,
                source_key=marker_key,
                digest=marker_digest,
            )

    count += link_explicit_prompt_ids(conn)
    return count

@_transactional
def link_explicit_prompt_ids(conn: sqlite3.Connection) -> int:
    """Create only deterministic links backed by literal PROMPT_ID markers."""
    count = 0
    rows = conn.execute(
        """SELECT p.prompt_uid,p.role,p.prompt_text,p.source_key
           FROM link_pending q CROSS JOIN prompts p ON p.prompt_uid=q.prompt_uid
           WHERE p.source='chatgpt'"""
    )
    with atomic(conn):
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
            conn.execute("DELETE FROM link_pending WHERE prompt_uid=?", (row["prompt_uid"],))

    return count
