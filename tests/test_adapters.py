from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from prompt_history.adapters import (
    ingest_chatgpt_export,
    ingest_chatgpt_exporter_archive,
    ingest_codex_usage,
    ingest_roadmap,
    ingest_session_bandit_export,
    link_explicit_prompt_ids,
)
from prompt_history.store import connect, init_db


class AdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.conn = connect(self.root / "history.sqlite")
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_chatgpt_export_ingests_messages_and_parent_relation(self) -> None:
        path = self.root / "conversations.json"
        path.write_text(json.dumps([{
            "id": "conv-1",
            "title": "Test",
            "mapping": {
                "a": {
                    "parent": None,
                    "message": {
                        "id": "m1",
                        "author": {"role": "user"},
                        "content": {"parts": ["first prompt"]},
                        "create_time": 1,
                        "metadata": {},
                    },
                },
                "b": {
                    "parent": "a",
                    "message": {
                        "id": "m2",
                        "author": {"role": "assistant"},
                        "content": {"parts": ["answer"]},
                        "create_time": 2,
                        "metadata": {"model_slug": "gpt-test"},
                    },
                },
            },
        }]), encoding="utf-8")
        ingest_chatgpt_export(self.conn, path)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM prompts").fetchone()[0], 2)
        relation = self.conn.execute("SELECT relation_type FROM relations").fetchone()[0]
        self.assertEqual(relation, "conversation_parent")

    def test_explicit_prompt_ids_link_chatgpt_to_codex(self) -> None:
        path = self.root / "links.json"
        path.write_text(json.dumps([{
            "id": "conv-links",
            "title": "Generated prompt",
            "mapping": {
                "a": {
                    "parent": None,
                    "message": {
                        "id": "m1",
                        "author": {"role": "assistant"},
                        "content": {"parts": [
                            "PROMPT_ID=333333 | PARENT_PROMPT_ID=222222\nDo the thing."
                        ]},
                        "create_time": 1,
                        "metadata": {},
                    },
                },
            },
        }]), encoding="utf-8")
        ingest_chatgpt_export(self.conn, path)
        relations = {
            row["relation_type"]
            for row in self.conn.execute("SELECT relation_type FROM relations")
        }
        self.assertIn("generated", relations)
        self.assertIn("parent", relations)
        self.assertIsNotNone(
            self.conn.execute("SELECT 1 FROM prompts WHERE prompt_id='333333'").fetchone()
        )
        self.assertIsNotNone(
            self.conn.execute("SELECT 1 FROM prompts WHERE prompt_id='222222'").fetchone()
        )

    def test_chatgpt_exporter_archive_ingests_normalized_graph(self) -> None:
        archive = self.root / "ChatGPTExport-test"
        conv_dir = archive / "conversations" / "conv-web"
        conv_dir.mkdir(parents=True)
        (conv_dir / "conversation.json").write_text(json.dumps({
            "schemaVersion": 1,
            "normalizerVersion": "chatgpt-web-v1",
            "provider": "chatgpt-web",
            "conversationId": "conv-web",
            "workspaceFingerprint": "workspace-test",
            "title": "Web export",
            "memberships": [{"scope": "main"}],
            "messages": [
                {
                    "id": "m1",
                    "nodeId": "n1",
                    "role": "user",
                    "parentId": None,
                    "createTime": 1,
                    "modelSlug": None,
                    "parts": [{"kind": "text", "text": "PROMPT_ID=333333 do it"}],
                },
                {
                    "id": "m2",
                    "nodeId": "n2",
                    "role": "assistant",
                    "parentId": "n1",
                    "createTime": 2,
                    "modelSlug": "gpt-test",
                    "parts": [{"kind": "text", "text": "done"}],
                },
            ],
        }), encoding="utf-8")

        ingest_chatgpt_exporter_archive(self.conn, archive)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM prompts WHERE source='chatgpt'").fetchone()[0],
            2,
        )
        self.assertIsNotNone(
            self.conn.execute(
                "SELECT 1 FROM relations WHERE relation_type='conversation_parent'"
            ).fetchone()
        )
        self.assertIsNotNone(
            self.conn.execute(
                "SELECT 1 FROM relations WHERE relation_type='references_prompt'"
            ).fetchone()
        )
        metadata = json.loads(self.conn.execute(
            "SELECT metadata_json FROM prompts WHERE source='chatgpt' AND message_id='m2'"
        ).fetchone()[0])
        self.assertEqual(metadata["upstream"], "siraht/ChatGPTExporter")
        self.assertEqual(metadata["model_slug"], "gpt-test")

    def test_chatgpt_exporter_parent_ingests_multiple_archives_idempotently(self) -> None:
        for suffix, conversation_id in (("one", "conv-one"), ("two", "conv-two")):
            conv_dir = self.root / f"ChatGPTExport-{suffix}" / "conversations" / conversation_id
            conv_dir.mkdir(parents=True)
            (conv_dir / "conversation.json").write_text(json.dumps({
                "schemaVersion": 1,
                "provider": "chatgpt-web",
                "conversationId": conversation_id,
                "workspaceFingerprint": suffix,
                "title": suffix,
                "messages": [{
                    "id": "m1",
                    "nodeId": "n1",
                    "role": "user",
                    "parentId": None,
                    "createTime": 1,
                    "parts": [{"kind": "text", "text": f"message {suffix}"}],
                }],
            }), encoding="utf-8")

        first = ingest_chatgpt_exporter_archive(self.conn, self.root)
        second = ingest_chatgpt_exporter_archive(self.conn, self.root)

        self.assertGreater(first, 0)
        self.assertEqual(second, 0)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM prompts WHERE source='chatgpt'").fetchone()[0],
            2,
        )

    def test_session_bandit_ingests_codex_transcript_without_execution_duplication(self) -> None:
        path = self.root / "session-bandit.jsonl"
        path.write_text(json.dumps({
            "agent": "codex",
            "sessionId": "session-1",
            "filePath": "/tmp/rollout.jsonl",
            "project": "/tmp/project",
            "cwd": "/tmp/project",
            "startedAt": "2026-09-22T00:00:00Z",
            "endedAt": "2026-09-22T00:10:00Z",
            "model": "gpt-5.6-terra",
            "messageCount": 2,
            "messages": [
                {
                    "role": "user",
                    "text": "PROMPT_ID=444444\nFix it.",
                    "toolCalls": [],
                    "timestamp": "2026-09-22T00:00:00Z",
                },
                {
                    "role": "assistant",
                    "text": "Fixed.",
                    "toolCalls": [{"name": "shell", "input": {}, "status": "ok", "output": "ok"}],
                    "timestamp": "2026-09-22T00:01:00Z",
                },
            ],
            "stats": {"totalInputTokens": 10, "totalOutputTokens": 2},
        }) + "\n", encoding="utf-8")

        ingest_session_bandit_export(self.conn, path)
        self.assertIsNotNone(
            self.conn.execute("SELECT 1 FROM prompts WHERE prompt_id='444444'").fetchone()
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0],
            0,
        )
        self.assertIsNotNone(
            self.conn.execute(
                "SELECT 1 FROM relations WHERE source='codex-session-bandit' "
                "AND relation_type='conversation_parent'"
            ).fetchone()
        )
        assistant = self.conn.execute(
            "SELECT metadata_json FROM prompts "
            "WHERE source='codex-session-bandit' AND role='assistant'"
        ).fetchone()
        self.assertIsNotNone(assistant)
        self.assertEqual(json.loads(assistant[0])["upstream"], "janole/session-bandit")

    def test_codex_usage_metrics_ingest(self) -> None:
        (self.root / "index").mkdir()
        cycle = "abc123"
        rel = Path("prompts") / "123456" / "cycles" / cycle
        (self.root / rel).mkdir(parents=True)
        (self.root / "index" / "prompts.jsonl").write_text(
            json.dumps({
                "chat_id": 7,
                "cycle_key": cycle,
                "path": str(rel),
                "prompt_id": "123456",
                "status": "PASS",
            }) + "\n",
            encoding="utf-8",
        )
        (self.root / rel / "metrics.json").write_text(json.dumps({
            "cycle_key": cycle,
            "prompt_id": "123456",
            "prompt_text_redacted": "prompt",
            "status": "PASS",
            "model": "gpt-5.6-terra",
            "reasoning_effort": "medium",
            "input_tokens": 100,
            "cached_input_tokens": 80,
            "output_tokens": 20,
            "total_tokens": 120,
            "tool_call_count": 3,
            "duration_seconds": 12.5,
        }), encoding="utf-8")
        ingest_codex_usage(self.conn, self.root)
        row = self.conn.execute(
            """SELECT p.prompt_uid,e.variant,e.total_tokens,e.result
               FROM prompts p JOIN executions e ON e.prompt_uid=p.prompt_uid"""
        ).fetchone()
        self.assertEqual(row["prompt_uid"], "codex:123456")
        self.assertEqual(row["variant"], "Terra")
        self.assertEqual(row["total_tokens"], 120)
        self.assertEqual(row["result"], "PASS")

    def test_roadmap_ingests_canonical_prompt_execution_and_fix(self) -> None:
        path = self.root / "roadmap.sqlite"
        src = sqlite3.connect(path)
        src.executescript("""
        CREATE TABLE prompts(
          prompt_id TEXT PRIMARY KEY,title TEXT,project_id TEXT,repo TEXT,prompt_type TEXT,
          created_at TEXT,explanation TEXT
        );
        CREATE TABLE prompt_materializations(prompt_id TEXT PRIMARY KEY,body TEXT);
        CREATE TABLE executions(
          execution_id INTEGER PRIMARY KEY, prompt_id TEXT, cycle_key TEXT, model TEXT,
          reasoning TEXT,duration_seconds REAL,input_tokens INTEGER,output_tokens INTEGER,
          cached_input_tokens INTEGER,total_tokens INTEGER,tool_call_count INTEGER,
          outcome TEXT,started_at TEXT,ended_at TEXT
        );
        CREATE TABLE prompt_relations(
          from_prompt_id TEXT,to_prompt_id TEXT,relation_type TEXT,created_at TEXT,actor TEXT,note TEXT
        );
        CREATE TABLE dependencies(prompt_id TEXT,depends_on_prompt_id TEXT,note TEXT);
        CREATE TABLE analyses(
          analysis_id INTEGER PRIMARY KEY,prompt_id TEXT,fix_prompt_id TEXT,analyzed_at TEXT,
          summary TEXT,source_ref TEXT,bottlenecks_found INTEGER
        );
        CREATE TABLE artifacts(
          artifact_id INTEGER PRIMARY KEY,prompt_id TEXT,artifact_type TEXT,uri TEXT,
          label TEXT,created_at TEXT
        );
        """)
        src.execute(
            "INSERT INTO prompts VALUES(?,?,?,?,?,?,?)",
            ("111111", "A", "49", "PersonalHub", "Prompt", "2026-01-01Z", "fallback"),
        )
        src.execute(
            "INSERT INTO prompts VALUES(?,?,?,?,?,?,?)",
            ("222222", "B", "49", "PersonalHub", "Prompt", "2026-01-02Z", "fix"),
        )
        src.execute("INSERT INTO prompt_materializations VALUES(?,?)", ("111111", "full body"))
        src.execute(
            "INSERT INTO executions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (1, "111111", "cycle-1", "GPT-5.6 Terra", "medium", 5, 10, 2, 8, 12, 1, "BLOCKED", "s", "e"),
        )
        src.execute(
            "INSERT INTO executions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (2, "222222", "cycle-2", "GPT-5.6 Terra", "medium", 4, 8, 2, 6, 10, 1, "PASS", "s2", "e2"),
        )
        src.execute(
            "INSERT INTO analyses VALUES(?,?,?,?,?,?,?)",
            (1, "111111", "222222", "t", "summary", "ref", 1),
        )
        src.commit()
        src.close()

        ingest_roadmap(self.conn, path)
        row = self.conn.execute(
            "SELECT prompt_text,text_quality FROM prompts WHERE prompt_id='111111'"
        ).fetchone()
        self.assertEqual(row["prompt_text"], "full body")
        self.assertEqual(row["text_quality"], 100)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0], 2)
        relation = self.conn.execute(
            "SELECT relation_type FROM relations WHERE relation_type='fix_prompt'"
        ).fetchone()
        self.assertIsNotNone(relation)
        resolved = self.conn.execute(
            "SELECT relation_type FROM relations WHERE relation_type='resolved_by'"
        ).fetchone()
        self.assertIsNotNone(resolved)


if __name__ == "__main__":
    unittest.main()
