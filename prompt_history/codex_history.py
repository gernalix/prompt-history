"""Read the app's persisted item history without modifying its databases."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .chatgpt_adapters import _iso_from_epoch
from .incremental import atomic
from .store import ingest_record


def ingest_codex_history(conn: sqlite3.Connection, path: str | Path) -> int:
    path = Path(path).expanduser().resolve()
    source = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    count = 0
    try:
        source.execute("BEGIN")
        rows = source.execute("SELECT * FROM thread_items ORDER BY thread_id,rollout_ordinal,item_id")
        with atomic(conn):
            for row in rows:
                item = json.loads(row["item_json"])
                kind = item.get("type", row["item_type"])
                # Hidden model reasoning is not a public transcript message.
                if kind == "reasoning":
                    continue
                role = "user" if kind == "userMessage" else "assistant" if kind == "agentMessage" else "tool"
                if kind == "userMessage":
                    text = "\n".join(p.get("text", "") for p in item.get("content", []) if isinstance(p, dict) and isinstance(p.get("text"), str))
                elif kind == "agentMessage":
                    text = item.get("text", "")
                else:
                    text = json.dumps(item, ensure_ascii=False, sort_keys=True)
                key = f"{row['thread_id']}:{row['turn_id']}:{row['item_id']}"
                count += int(ingest_record(conn, {
                    "kind": "prompt", "source": "codex-native-history", "source_key": key,
                    "conversation_id": row["thread_id"], "message_id": row["item_id"],
                    "role": role, "prompt_text": text, "text_quality": 100,
                    "created_at": _iso_from_epoch(row["created_at_ms"] / 1000),
                    "metadata": {"file_path": str(path), "turn_id": row["turn_id"],
                                 "ordinal": row["rollout_ordinal"], "item_type": kind,
                                 "started_at_ms": row["started_at_ms"],
                                 "completed_at_ms": row["completed_at_ms"], "item": item},
                }))
    finally:
        source.close()
    return count
