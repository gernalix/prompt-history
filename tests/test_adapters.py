from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from prompt_history.adapters import ingest_chatgpt_export, ingest_codex_usage, ingest_roadmap
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
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0], 1)
        relation = self.conn.execute(
            "SELECT relation_type FROM relations WHERE relation_type='fix_prompt'"
        ).fetchone()
        self.assertIsNotNone(relation)


if __name__ == "__main__":
    unittest.main()
