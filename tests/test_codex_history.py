import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from prompt_history.codex_history import ingest_codex_history
from prompt_history.store import connect, init_db

class NativeHistoryTests(unittest.TestCase):
    def test_recovers_migrated_items_updates_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = sqlite3.connect(root / "native.sqlite")
            src.execute("CREATE TABLE thread_items(thread_id,turn_id,item_id,rollout_ordinal,created_at_ms,item_json,item_type,started_at_ms,completed_at_ms)")
            def add(i, item):
                src.execute("INSERT INTO thread_items VALUES(?,?,?,?,?,?,?,?,?)", ("old-chat", "turn", str(i), i, 1790000000123, json.dumps(item), item["type"], 1, 2))
            add(1, {"type":"userMessage", "content":[{"type":"text", "text":"question"}]})
            add(2, {"type":"agentMessage", "text":"intermediate", "phase":"commentary"})
            add(3, {"type":"commandExecution", "command":"example", "aggregatedOutput":"full output"})
            add(4, {"type":"reasoning", "text":"hidden"})
            src.commit()
            conn = connect(root / "derived.sqlite")
            init_db(conn)
            self.assertEqual(ingest_codex_history(conn, root / "native.sqlite"), 3)
            self.assertEqual(ingest_codex_history(conn, root / "native.sqlite"), 0)
            self.assertEqual(conn.execute("SELECT count(*) FROM prompts").fetchone()[0], 3)
            row = conn.execute("SELECT * FROM prompts WHERE message_id='2'").fetchone()
            self.assertEqual(row["prompt_text"], "intermediate")
            self.assertTrue(row["created_at"].endswith(".123000Z"))
            src.execute("UPDATE thread_items SET item_json=? WHERE item_id='2'", (json.dumps({"type":"agentMessage","text":"corrected"}),))
            src.commit()
            self.assertEqual(ingest_codex_history(conn, root / "native.sqlite"), 1)
            self.assertEqual(conn.execute("SELECT count(*) FROM prompts").fetchone()[0], 3)
            self.assertEqual(conn.execute("SELECT prompt_text FROM prompts WHERE message_id='2'").fetchone()[0], "corrected")
            self.assertEqual(src.execute("SELECT count(*) FROM thread_items").fetchone()[0], 4)
            src.execute("INSERT INTO thread_items VALUES('bad','t','broken',0,0,'invalid','agentMessage',NULL,NULL)")
            src.commit()
            with self.assertRaises(json.JSONDecodeError):
                ingest_codex_history(conn, root / "native.sqlite")
            self.assertEqual(conn.execute("SELECT count(*) FROM prompts").fetchone()[0], 3)
            conn.close()
            src.close()
