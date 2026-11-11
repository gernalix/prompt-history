from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from prompt_history.store import connect, ingest_record, init_db
from tools.publish_transcripts import publish


class StoreTests(unittest.TestCase):
    def test_new_warehouse_is_private_with_permissive_umask(self) -> None:
        root = Path(self.tmp.name) / "private"
        previous = os.umask(0)
        try:
            conn = connect(root / "index.sqlite")
            conn.close()
        finally:
            os.umask(previous)
        self.assertEqual(root.stat().st_mode & 0o777, 0o700)
        self.assertEqual((root / "index.sqlite").stat().st_mode & 0o777, 0o600)

    def test_failed_transcript_producer_preserves_previous_output(self) -> None:
        path = Path(self.tmp.name) / "transcripts.jsonl"
        path.write_text("previous fixture\n")
        with self.assertRaises(subprocess.CalledProcessError):
            publish(path, [sys.executable, "-c", "print('partial fixture'); raise SystemExit(1)"])
        self.assertEqual(path.read_text(), "previous fixture\n")
        publish(path, [sys.executable, "-c", "print('new fixture')"])
        self.assertEqual(path.read_text(), "new fixture\n")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "history.sqlite"
        self.conn = connect(self.db)
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_prompt_id_merges_sources_and_keeps_best_text(self) -> None:
        ingest_record(self.conn, {
            "kind": "prompt",
            "source": "codex-usage",
            "source_key": "cycle-a",
            "prompt_id": "123456",
            "prompt_text": "redacted",
            "text_quality": 40,
        })
        ingest_record(self.conn, {
            "kind": "prompt",
            "source": "codex-roadmap",
            "source_key": "123456",
            "prompt_id": "123456",
            "prompt_text": "canonical full prompt",
            "text_quality": 100,
            "repo": "PersonalHub",
        })
        ingest_record(self.conn, {
            "kind": "prompt",
            "source": "codex-usage",
            "source_key": "cycle-b",
            "prompt_id": "123456",
            "prompt_text": "later redacted",
            "text_quality": 40,
        })
        row = self.conn.execute(
            "SELECT * FROM prompts WHERE prompt_id='123456'"
        ).fetchone()
        self.assertEqual(row["prompt_uid"], "codex:123456")
        self.assertEqual(row["prompt_text"], "canonical full prompt")
        self.assertEqual(row["repo"], "PersonalHub")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM prompts").fetchone()[0],
            1,
        )

    def test_exact_source_payload_is_idempotent(self) -> None:
        record = {
            "kind": "prompt",
            "source": "chatgpt",
            "source_key": "c:m",
            "prompt_text": "hello",
            "text_quality": 100,
        }
        self.assertTrue(ingest_record(self.conn, record))
        self.assertFalse(ingest_record(self.conn, record))
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM source_records").fetchone()[0],
            1,
        )

    def test_execution_creates_placeholder_then_prompt_can_enrich_it(self) -> None:
        ingest_record(self.conn, {
            "kind": "execution",
            "source": "codex-usage",
            "source_key": "cycle-x",
            "prompt_id": "654321",
            "model": "gpt-5.6-terra",
            "reasoning": "medium",
            "result": "PASS",
        })
        ingest_record(self.conn, {
            "kind": "prompt",
            "source": "codex-roadmap",
            "source_key": "654321",
            "prompt_id": "654321",
            "prompt_text": "real prompt",
            "text_quality": 100,
        })
        row = self.conn.execute(
            """SELECT p.prompt_text,e.result
               FROM prompts p JOIN executions e ON e.prompt_uid=p.prompt_uid
               WHERE p.prompt_id='654321'"""
        ).fetchone()
        self.assertEqual(dict(row), {"prompt_text": "real prompt", "result": "PASS"})


if __name__ == "__main__":
    unittest.main()
